#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2020 Seagate Technology LLC and/or its Affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.

"""
Provisioner utiltiy methods for Deployment of k8s based Cortx Deployment
"""
import csv

import json
import logging
import os
import time
from typing import List

import yaml

from commons import commands as common_cmd
from commons import constants as common_const
from commons import pswdmanager
from commons.helpers.pods_helper import LogicalNode
from commons.params import TEST_DATA_FOLDER
from commons.utils import system_utils, assert_utils, ext_lbconfig_utils
from config import PROV_CFG
from libs.csm.rest.csm_rest_s3user import RestS3user
from libs.prov.provisioner import Provisioner
from libs.s3 import S3H_OBJ
from libs.s3.s3_test_lib import S3TestLib
from scripts.s3_bench import s3bench

LOGGER = logging.getLogger(__name__)


class ProvDeployK8sCortxLib:
    """
    This class contains utility methods for all the operations related
    to k8s based Cortx Deployment .
    """

    def __init__(self):
        self.deploy_cfg = PROV_CFG["k8s_cortx_deploy"]
        self.git_script_tag = os.getenv("GIT_SCRIPT_TAG")
        self.cortx_image = os.getenv("CORTX_IMAGE")
        self.docker_username = os.getenv("DOCKER_USERNAME")
        self.docker_password = os.getenv("DOCKER_PASSWORD")
        self.test_dir_path = os.path.join(TEST_DATA_FOLDER, "testDeployment")

    @staticmethod
    def setup_k8s_cluster(master_node_list: list, worker_node_list: list,
                          taint_master: bool = True) -> tuple:
        """
        Setup k8s cluster using RE jenkins job
        param: master_node_list : List of all master nodes(Logical Node object)
        param: worker_node_list : List of all worker nodes(Logical Node object)
        param: taint_master : Taint master - boolean
        return : True/False and success/failure message
        """
        k8s_deploy_cfg = PROV_CFG["k8s_cluster_deploy"]
        LOGGER.info("Create inputs for RE jenkins job")
        hosts_input_str = []
        jen_parameter = {}
        if len(master_node_list) > 0:
            # TODO : handle multiple master node case.
            input_str = f'hostname={master_node_list[0].hostname},' \
                        f'user={master_node_list[0].username},' \
                        f'pass={master_node_list[0].password}'
            hosts_input_str.append(input_str)
        else:
            return False, "Master Node List is empty"

        if len(worker_node_list) > 0:
            for each in worker_node_list:
                input_str = f'hostname={each.hostname},' \
                            f'user={each.username},' \
                            f'pass={each.password}'
                hosts_input_str.append(input_str)
        else:
            return False, "Worker Node List is empty"
        hosts = "\n".join(each for each in hosts_input_str)
        jen_parameter["hosts"] = hosts
        jen_parameter["TAINT"] = taint_master

        output = Provisioner.build_job(
            k8s_deploy_cfg["job_name"], jen_parameter, k8s_deploy_cfg["auth_token"],
            k8s_deploy_cfg["jenkins_url"])
        LOGGER.info("Jenkins Build URL: {}".format(output['url']))
        if output['result'] == "SUCCESS":
            LOGGER.info("k8s Cluster Deployment successful")
            return True, output['result']
        else:
            LOGGER.error(f"k8s Cluster Deployment {output['result']},please check URL")
            return False, output['result']

    @staticmethod
    def validate_master_tainted(node_obj: LogicalNode) -> bool:
        """
        Validate master node tainted.
        param: node_obj: Master node object
        return: Boolean
        """
        LOGGER.info("Check if master is tainted")
        resp = node_obj.execute_cmd(common_cmd.K8S_CHK_TAINT.format(node_obj.hostname))
        LOGGER.debug("resp: %s", resp)
        if isinstance(resp, bytes):
            resp = str(resp, 'UTF-8')
        if "NoSchedule" in resp:
            LOGGER.info("%s is tainted", node_obj.hostname)
            return True
        LOGGER.info("%s is not tainted", node_obj.hostname)
        return False

    @staticmethod
    def taint_master(node_obj: LogicalNode):
        """
        Taint master node.
        param: node_obj: Master node object
        """
        LOGGER.info("Adding taint to %s", node_obj.hostname)
        resp = node_obj.execute_cmd(common_cmd.K8S_TAINT_NODE.format(node_obj.hostname))
        LOGGER.debug("resp: %s", resp)

    def prereq_vm(self, node_obj: LogicalNode) -> tuple:
        """
        Perform prerequisite check for VM configurations
        param: node_obj : Node object to perform the checks on
        return: True/False and success/failure message
        """
        LOGGER.info(
            "Starting the prerequisite checks on node %s",
            node_obj.hostname)
        LOGGER.info("Check that the host is pinging")
        node_obj.execute_cmd(cmd=
                             common_cmd.CMD_PING.format(node_obj.hostname), read_lines=True)
        LOGGER.info("Checking No of CPU's")
        resp = node_obj.execute_cmd(cmd=
                                    common_cmd.CMD_NUM_CPU,
                                    read_lines=True)[0].strip()
        LOGGER.info("No of CPU : %s", resp)
        if int(resp) < self.deploy_cfg["prereq"]["cpu_cores"]:
            return False, "No of CPU are not as expected."
        LOGGER.info("Checking number of disks present")
        count = node_obj.execute_cmd(cmd=common_cmd.CMD_LSBLK, read_lines=True)
        LOGGER.info("No. of disks : %s", count[0])
        if int(count[0]) < self.deploy_cfg["prereq"]["min_disks"]:
            return False, f"Need at least " \
                          f"{self.deploy_cfg['prereq']['min_disks']} disks for deployment"

        LOGGER.info("Checking OS release version")
        resp = node_obj.execute_cmd(cmd=
                                    common_cmd.CMD_OS_REL,
                                    read_lines=True)[0].strip()
        LOGGER.info("OS Release Version: %s", resp)
        if resp not in self.deploy_cfg["prereq"]["os_release"]:
            return False, "OS version is different than expected."

        LOGGER.info("Checking kernel version")
        resp = node_obj.execute_cmd(cmd=
                                    common_cmd.CMD_KRNL_VER,
                                    read_lines=True)[0].strip()
        LOGGER.info("Kernel Version: %s", resp)
        if resp not in self.deploy_cfg["prereq"]["kernel"]:
            return False, "Kernel Version is different than expected."

        return True, "Prerequisite VM check successful"

    @staticmethod
    def docker_login(node_obj: LogicalNode, docker_user: str, docker_pswd: str):
        """
        Perform Docker Login
        param: node_obj: Node Object
        param: docker_user : Docker username
        param: docker_pswd : Docker password
        """
        LOGGER.info("Perform Docker Login")
        resp = node_obj.execute_cmd(common_cmd.CMD_DOCKER_LOGIN.format(docker_user, docker_pswd))
        LOGGER.debug("resp: %s", resp)

    def prereq_git(self, node_obj: LogicalNode, git_tag: str):
        """
        Checkout cortx-k8s code on the  node. Delete is any previous exists.
        param: node_obj : Node object to checkout code - node.
        param: git tag: tag of service repo
        """
        LOGGER.info("Delete cortx-k8s repo on node")
        resp = node_obj.execute_cmd(common_cmd.CMD_REMOVE_DIR.format("cortx-k8s"))
        LOGGER.debug("resp: %s", resp)

        LOGGER.info("Git clone cortx-k8s repo")
        url = self.deploy_cfg["git_k8_repo"]
        resp = node_obj.execute_cmd(common_cmd.CMD_GIT_CLONE.format(url))
        LOGGER.debug("resp: %s", resp)

        LOGGER.info("Git checkout tag %s", git_tag)
        cmd = "cd cortx-k8s && " + common_cmd.CMD_GIT_CHECKOUT.format(git_tag)
        resp = node_obj.execute_cmd(cmd)
        LOGGER.debug("resp: %s", resp)

    def execute_prereq_cortx(self, node_obj: LogicalNode, remote_code_path: str, system_disk: str):
        """
        Execute prerq script on worker node,
        param: node_obj: Worker node object
        param: system_disk: parameter to prereq script
        """
        LOGGER.info("Execute prereq script")
        cmd = "cd {}; {} {}| tee prereq-deploy-cortx-cloud.log". \
            format(remote_code_path, self.deploy_cfg["exe_prereq"], system_disk)
        resp = node_obj.execute_cmd(cmd, read_lines=True)
        LOGGER.debug("\n".join(resp).replace("\\n", "\n"))

    @staticmethod
    def copy_sol_file(node_obj: LogicalNode, local_sol_path: str,
                      remote_code_path: str):
        """
        Copy Solution file from local path tp remote path
        """
        LOGGER.info("Copy Solution file to remote path")
        LOGGER.debug("Local path %s", local_sol_path)
        remote_path = remote_code_path + "solution.yaml"
        LOGGER.debug("Remote path %s", remote_path)
        if system_utils.path_exists(local_sol_path):
            node_obj.copy_file_to_remote(local_sol_path, remote_path)
            return True, f"File copied at {remote_path}"
        return False, f"{local_sol_path} not found"

    def deploy_cluster(self, node_obj: LogicalNode, remote_code_path: str) -> tuple:
        """
        Deploy cortx cluster.
        cortx-k8s repo code should be checked out on node at remote_code_path
        param: node_obj: Node object
        param: remote_code_path: Cortx-k8s repo Path on node
        return : True/False and resp
        """
        LOGGER.info("Deploy Cortx cloud")
        cmd = "cd {}; {} | tee deployment.log".format(remote_code_path,
                                                      self.deploy_cfg["deploy_cluster"])
        resp = node_obj.execute_cmd(cmd, read_lines=True)
        LOGGER.debug("\n".join(resp).replace("\\n", "\n"))
        return True, resp

    @staticmethod
    def validate_cluster_status(node_obj: LogicalNode, remote_code_path):
        """
        Validate cluster status
        param: node_obj : Logical node object of Master node
        param: remote_code_path: Remote code path of cortx-k8s repo on master node.
        return : Boolean
        """
        LOGGER.info("Validate Cluster status")
        cmd = "cd {}; {} | tee cluster_status.log".format(remote_code_path,
                                                          common_cmd.CLSTR_STATUS_CMD)
        resp = node_obj.execute_cmd(cmd, read_lines=True)
        LOGGER.debug("\n".join(resp).replace("\\n", "\n"))
        if "FAILED" in resp:
            return False, resp
        return True, resp

    def pull_third_party_images(self, worker_obj_list: list):
        """
        This method pulls  cortx and 3rd party images
        param: worker_obj_list: Worker Object list
        return : Boolean
        """
        LOGGER.info("Pull Cortx and 3rd party images on all worker nodes.")
        LOGGER.debug(self.deploy_cfg['third_party_images'])
        data = self.deploy_cfg['third_party_images']
        for obj in worker_obj_list:
            obj.execute_cmd(common_cmd.CMD_DOCKER_PULL.format(self.cortx_image))
            for key, value in data.items():
                if key in ("kafka", "zookeeper"):
                    value = "bitnami/" + key + ":" + value
                cmd = common_cmd.CMD_DOCKER_PULL.format(value)
                obj.execute_cmd(cmd=cmd)
        return True

    def deploy_cortx_cluster(self, sol_file_path: str, master_node_list: list,
                             worker_node_list: list, system_disk_dict: dict,
                             docker_username: str, docker_password: str,
                             git_tag) -> tuple:
        """
        Perform cortx cluster deployment
        param: solution_file_path: Local Solution file path
        param: master_node_list : List of all master nodes(Logical Node object)
        param: worker_node_list : List of all worker nodes(Logical Node object)
        param: docker_username: Docker Username
        param: docker_password: Docker password
        param: git tag: tag of service repo
        return : True/False and resp
        """
        if len(master_node_list) == 0:
            return False, "Minimum one master node needed for deployment"
        if len(worker_node_list) == 0:
            return False, "Minimum one worker node needed for deployment"

        for node in worker_node_list:
            resp = self.prereq_vm(node)
            assert_utils.assert_true(resp[0], resp[1])
            system_disk = system_disk_dict[node.hostname]
            self.docker_login(node, docker_username, docker_password)
            self.prereq_git(node, git_tag)
            self.copy_sol_file(node, sol_file_path, self.deploy_cfg["git_remote_dir"])
            # system disk will be used mount /mnt/fs-local-volume on worker node
            self.execute_prereq_cortx(node, self.deploy_cfg["git_remote_dir"], system_disk)
        self.pull_third_party_images(worker_node_list)

        self.docker_login(master_node_list[0], docker_username, docker_password)
        self.prereq_git(master_node_list[0], git_tag)
        self.copy_sol_file(master_node_list[0], sol_file_path, self.deploy_cfg["git_remote_dir"])
        resp = self.deploy_cluster(master_node_list[0], self.deploy_cfg["git_remote_dir"])
        if resp[0]:
            LOGGER.info("Validate cluster status using status-cortx-cloud.sh")
            resp = self.validate_cluster_status(master_node_list[0],
                                                self.deploy_cfg["git_remote_dir"])
            return resp
        return resp

    def checkout_solution_file(self, git_tag):
        """
        Method to checkout solution.yaml file
        param: git tag: tag of service repo
        """
        url = self.deploy_cfg["git_k8_repo_file"].format(git_tag)
        cmd = common_cmd.CMD_CURL.format(self.deploy_cfg["new_file_path"], url)
        system_utils.execute_cmd(cmd=cmd)
        return self.deploy_cfg["new_file_path"]

    def update_sol_yaml(self, worker_obj: list, filepath: str, cortx_image: str,
                        **kwargs):
        """
        This function updates the yaml file
        :Param: worker_obj: list of worker node object
        :Param: filepath: Filename with complete path
        :Param: cortx_image: this is cortx image name
        :Keyword: cvg_count: cvg_count per node
        :Keyword: type_cvg: ios or cas
        :Keyword: data_disk_per_cvg: data disk required per cvg
        :Keyword: size_metadata: size of metadata disk
        :Keyword: size_data_disk: size of data disk
        :Keyword: glusterfs_size: size of glusterfs
        :Keyword: sns_data: N
        :Keyword: sns_parity: K
        :Keyword: sns_spare: S
        :Keyword: dix_data:
        :Keyword: dix_parity:
        :Keyword: dix_spare:
        :Keyword: skip_disk_count_check: disk count check
        :Keyword: third_party_image: dict of third party image
        returns the status, filepath and system reserved disk
        """
        cvg_count = kwargs.get("cvg_count", 2)
        data_disk_per_cvg = kwargs.get("data_disk_per_cvg", 0)
        cvg_type = kwargs.get("cvg_type", "ios")
        sns_data = kwargs.get("sns_data", 1)
        sns_parity = kwargs.get("sns_parity", 0)
        sns_spare = kwargs.get("sns_spare", 0)
        dix_data = kwargs.get("dix_data", 1)
        dix_parity = kwargs.get("dix_parity", 0)
        dix_spare = kwargs.get("dix_spare", 0)
        size_metadata = kwargs.get("size_metadata", '20Gi')
        size_data_disk = kwargs.get("size_data_disk", '20Gi')
        glusterfs_size = kwargs.get("glusterfs_size", '20Gi')
        skip_disk_count_check = kwargs.get("skip_disk_count_check", False)
        third_party_images_dict = kwargs.get("third_party_images",
                                             self.deploy_cfg['third_party_images'])
        data_devices = list()  # empty list for data disk
        sys_disk_pernode = {}  # empty dict
        node_list = len(worker_obj)
        valid_disk_count = sns_spare + sns_data + sns_parity
        metadata_devices = []
        for node_count, node_obj in enumerate(worker_obj, start=1):
            LOGGER.info(node_count)
            device_list = node_obj.execute_cmd(cmd=common_cmd.CMD_LIST_DEVICES,
                                               read_lines=True)[0].split(",")
            device_list[-1] = device_list[-1].replace("\n", "")
            metadata_devices = device_list[1:cvg_count + 1]
            # This will split the metadata disk list
            # into metadata devices per cvg
            # 2 is defined the split size based
            # on disk required for metadata,system
            device_list_len = len(device_list)
            new_device_lst_len = (device_list_len - cvg_count - 1)
            count = cvg_count
            if data_disk_per_cvg == 0:
                data_disk_per_cvg = int(len(device_list[cvg_count + 1:]) / cvg_count)

            LOGGER.debug("Data disk per cvg : %s", data_disk_per_cvg)
            # The condition to validate the config.
            if not skip_disk_count_check and valid_disk_count > \
                    (data_disk_per_cvg * cvg_count * node_list):
                return False, "The sum of data disks per cvg " \
                              "is less than N+K+S count"

            if new_device_lst_len < data_disk_per_cvg * cvg_count:
                return False, "The requested data disk is more than" \
                              " the data disk available on the system"
            # This condition validated the total available disk count
            # and split the disks per cvg.
            data_devices_f = device_list[cvg_count + 1:]
            if (data_disk_per_cvg * cvg_count) < new_device_lst_len:
                count_end = int(data_disk_per_cvg)
                data_devices.append(data_devices_f[0:count_end])
                while count:
                    count = count - 1
                    new_end = int(count_end + data_disk_per_cvg)
                    if new_end > new_device_lst_len:
                        break
                    data_devices_ad = data_devices_f[count_end:new_end]
                    count_end = int(count_end + data_disk_per_cvg)
                    data_devices.append(data_devices_ad)
            else:
                LOGGER.debug("Data devices : else : %s", data_devices_f)
                LOGGER.debug("data disk per cvg : %s", data_disk_per_cvg)
                data_devices = [data_devices_f[i:i + data_disk_per_cvg]
                                for i in range(0, len(data_devices_f), data_disk_per_cvg)]

            # Create dict for host and disk
            system_disk = device_list[0]
            schema = {node_obj.hostname: system_disk}
            sys_disk_pernode.update(schema)
        LOGGER.info("Metadata disk %s", metadata_devices)
        LOGGER.info("data disk %s", data_devices)
        # Update the solution yaml file with password
        resp_passwd = self.update_password_sol_file(filepath)
        if not resp_passwd[0]:
            return False, "Failed to update passwords in solution file"
        # Update the solution yaml file with images
        resp_image = self.update_image_section_sol_file(filepath, cortx_image,
                                                        third_party_images_dict)
        if not resp_image[0]:
            return False, "Failed to update images in solution file"

        # Update the solution yaml file with cvg
        resp_cvg = self.update_cvg_sol_file(filepath, metadata_devices,
                                            data_devices,
                                            cvg_count,
                                            cvg_type,
                                            data_disk_per_cvg,
                                            sns_data,
                                            sns_parity,
                                            sns_spare,
                                            dix_data,
                                            dix_parity,
                                            dix_spare,
                                            size_metadata,
                                            size_data_disk,
                                            glusterfs_size)
        if not resp_cvg[0]:
            return False, "Fail to update the cvg details in solution file"

        # Update the solution yaml file with node
        resp_node = self.update_nodes_sol_file(filepath, worker_obj)
        if not resp_node[0]:
            return False, "Failed to update nodes details in solution file"

        return True, filepath, sys_disk_pernode

    def update_nodes_sol_file(self, filepath, worker_obj):
        """
        Method to update the nodes section in solution.yaml
        Param: filepath: Filename with complete path
        Param: worker_obj: list of node object
        :returns the filepath and status True
        """
        node_list = len(worker_obj)
        with open(filepath) as soln:
            conf = yaml.safe_load(soln)
            parent_key = conf['solution']  # Parent key
            node = parent_key['nodes']  # Child Key
            total_nodes = node.keys()
            # Removing the elements from the node dict
            for key_count in list(total_nodes):
                node.pop(key_count)
            # Updating the node dict
            for item, host in zip(list(range(node_list)), worker_obj):
                dict_node = {}
                name = {'name': host.hostname}
                dict_node.update(name)
                new_node = {'node{}'.format(item + 1): dict_node}
                node.update(new_node)
            conf['solution']['nodes'] = node
            soln.close()
        noalias_dumper = yaml.dumper.SafeDumper
        noalias_dumper.ignore_aliases = lambda self, data: True
        with open(filepath, 'w') as soln:
            yaml.dump(conf, soln, default_flow_style=False,
                      sort_keys=False, Dumper=noalias_dumper)
            soln.close()
        return True, filepath

    def update_cvg_sol_file(self, filepath,
                            metadata_devices: list,
                            data_devices: list,
                            cvg_count: int,
                            cvg_type: str,
                            data_disk_per_cvg: int,
                            sns_data: int,
                            sns_parity: int,
                            sns_spare: int,
                            dix_data: int,
                            dix_parity: int,
                            dix_spare: int,
                            size_metadata: str,
                            size_data_disk: str,
                            glusterfs_size: str):

        """
        Method to update the cvg
        :Param: metadata_devices: list of meta devices
        :Param: data_devices: list of data devices
        :Param: filepath: file with complete path
        :Param: cvg_count: cvg_count per node
        :Param: type_cvg: ios or cas
        :Param: data_disk_per_cvg: data disk required per cvg
        :Param: sns_data: N
        :Param: sns_parity: K
        :Param: sns_spare: S
        :Param: dix_data:
        :Param: dix_parity:
        :Param: dix_spare:
        :Param: size_metadata: size of metadata disk
        :Param: size_data_disk: size of data disk
        :Param: glusterfs_size: size of glusterfs
        :returns the status ,filepath
        """
        nks = "{}+{}+{}".format(sns_data, sns_parity, sns_spare)  # Value of N+K+S for sns
        dix = "{}+{}+{}".format(dix_data, dix_parity, dix_spare)  # Value of N+K+S for dix
        with open(filepath) as soln:
            conf = yaml.safe_load(soln)
            parent_key = conf['solution']  # Parent key
            common = parent_key['common']  # Parent key
            storage = parent_key['storage']  # child of child key
            cmn_storage_sets = common['storage_sets']  # child of child key
            common['glusterfs']['size'] = glusterfs_size
            total_cvg = storage.keys()
            # SNS and dix value update
            cmn_storage_sets['durability']['sns'] = nks
            cmn_storage_sets['durability']['dix'] = dix
            for cvg_item in list(total_cvg):
                storage.pop(cvg_item)
            for cvg in range(0, cvg_count):
                cvg_dict = {}
                metadata_schema_upd = {'device': metadata_devices[cvg], 'size': size_metadata}
                data_schema = {}
                for disk in range(0, data_disk_per_cvg):
                    disk_schema_upd = {'device': data_devices[cvg][disk], 'size': size_data_disk}
                    c_data_device_schema = {'d{}'.format(disk + 1): disk_schema_upd}
                    data_schema.update(c_data_device_schema)
                c_device_schema = {'metadata': metadata_schema_upd, 'data': data_schema}
                key_cvg_devices = {'devices': c_device_schema}
                cvg_name = {'name': 'cvg-0{}'.format(cvg + 1)}
                cvg_type_schema = {'type': cvg_type}
                cvg_dict.update(cvg_name)
                cvg_dict.update(cvg_type_schema)
                cvg_dict.update(key_cvg_devices)
                cvg_key = {'cvg{}'.format(cvg + 1): cvg_dict}
                storage.update(cvg_key)
            conf['solution']['storage'] = storage
            LOGGER.debug("Storage Details : %s", storage)
            soln.close()
        noalias_dumper = yaml.dumper.SafeDumper
        noalias_dumper.ignore_aliases = lambda self, data: True
        with open(filepath, 'w') as soln:
            yaml.dump(conf, soln, default_flow_style=False,
                      sort_keys=False, Dumper=noalias_dumper)
            soln.close()
        return True, filepath

    def update_image_section_sol_file(self, filepath, cortx_image, third_party_images_dict):
        """
        Method use to update the Images section in solution.yaml
        Param: filepath: filename with complete path
        cortx_image: this is cortx image name
        third_party_image: dict of third party image
        :returns the status, filepath
        """
        cortx_im = dict()
        image_default_dict = {}
        image_default_dict.update(self.deploy_cfg['third_party_images'])

        for image_key in self.deploy_cfg['cortx_images_key']:
            cortx_im[image_key] = cortx_image
        with open(filepath) as soln:
            conf = yaml.safe_load(soln)
            parent_key = conf['solution']  # Parent key
            image = parent_key['images']  # Parent key
            conf['solution']['images'] = image
            image.update(cortx_im)
            for key, value in list(third_party_images_dict.items()):
                if key in list(self.deploy_cfg['third_party_images'].keys()):
                    image.update({key: value})
                    image_default_dict.pop(key)
            image.update(image_default_dict)
            soln.close()
        LOGGER.debug("Images used for deployment : %s", image)
        noalias_dumper = yaml.dumper.SafeDumper
        noalias_dumper.ignore_aliases = lambda self, data: True
        with open(filepath, 'w') as soln:
            yaml.dump(conf, soln, default_flow_style=False,
                      sort_keys=False, Dumper=noalias_dumper)
            soln.close()
        return True, filepath

    def update_password_sol_file(self, filepath):
        """
        This Method update the password in solution.yaml file
        Param: filepath: filename with complete path
        :returns the status, filepath
        """
        with open(filepath) as soln:
            conf = yaml.safe_load(soln)
            parent_key = conf['solution']  # Parent key
            content = parent_key['secrets']['content']
            common = parent_key['common']
            common['storage_provisioner_path'] = self.deploy_cfg['local_path_prov']
            common['s3']['max_start_timeout'] = self.deploy_cfg['s3_max_start_timeout']
            passwd_dict = {}
            for key, value in self.deploy_cfg['password'].items():
                passwd_dict[key] = pswdmanager.decrypt(value)
            content.update(passwd_dict)
            soln.close()
        noalias_dumper = yaml.dumper.SafeDumper
        noalias_dumper.ignore_aliases = lambda self, data: True
        with open(filepath, 'w') as soln:
            yaml.dump(conf, soln, default_flow_style=False,
                      sort_keys=False, Dumper=noalias_dumper)
            soln.close()
        return True, filepath

    @staticmethod
    def deploy_cortx_k8s_cluster(master_node_list: list, worker_node_list: list,
                                 deploy_target: str = "CORTX-CLUSTER") -> tuple:
        """
        Setup k8s cluster using RE jenkins job
        param: master_node_list : List of all master nodes(Logical Node object)
        param: worker_node_list : List of all worker nodes(Logical Node object)
        param: deploy_target : Only Third Party Components or Cortx Cluster
        return : True/False and success/failure message
        """
        k8s_deploy_cfg = PROV_CFG["k8s_cluster_deploy"]
        LOGGER.info("Create inputs for RE jenkins job")
        hosts_input_str = []
        jen_parameter = {}
        if len(master_node_list) > 0:
            input_str = f'hostname={master_node_list[0].hostname},' \
                        f'user={master_node_list[0].username},' \
                        f'pass={master_node_list[0].password}'
            hosts_input_str.append(input_str)
        else:
            return False, "Master Node List is empty"

        if len(worker_node_list) > 0:
            for each in worker_node_list:
                input_str = f'hostname={each.hostname},' \
                            f'user={each.username},' \
                            f'pass={each.password}'
                hosts_input_str.append(input_str)
        else:
            return False, "Worker Node List is empty"
        hosts = "\n".join(each for each in hosts_input_str)
        jen_parameter["hosts"] = hosts
        jen_parameter["DEPLOY_TARGET"] = deploy_target

        output = Provisioner.build_job(
            k8s_deploy_cfg["cortx_job_name"], jen_parameter, k8s_deploy_cfg["auth_token"],
            k8s_deploy_cfg["jenkins_url"])
        LOGGER.info("Jenkins Build URL: {}".format(output['url']))
        if output['result'] == "SUCCESS":
            LOGGER.info("k8s Cluster Deployment successful")
            return True, output['result']
        else:
            LOGGER.error(f"k8s Cluster Deployment {output['result']},please check URL")
            return False, output['result']

    @staticmethod
    def get_hctl_status(node_obj, pod_name: str) -> tuple:
        """
        Get hctl status for cortx.
        param: master_node: Master node(Logical Node object)
        param: pod_name: Running Data Pod
        return: True/False and success/failure message
        """
        LOGGER.info("Get Cluster status")
        cluster_status = node_obj.execute_cmd(cmd=common_cmd.K8S_HCTL_STATUS.
                                              format(pod_name)).decode('UTF-8')
        cluster_status = json.loads(cluster_status)
        if cluster_status is not None:
            nodes_data = cluster_status["nodes"]
            for node_data in nodes_data:
                services = node_data["svcs"]
                for svc in services:
                    if svc["status"] != "started":
                        return False, "Service {} not started.".format(svc["name"])
            return True, "Cluster is up and running."
        return False, "Cluster status is not retrieved."

    def destroy_setup(self, master_node_obj: LogicalNode, worker_node_obj: list):
        """
        Method used to run destroy script
        param: master node obj list
        param: worker node obj list
        """
        cmd1 = "cd {} && {} --force".format(self.deploy_cfg["git_remote_dir"],
                                            self.deploy_cfg["destroy_cluster"])
        cmd2 = "umount {}".format(self.deploy_cfg["local_path_prov"])
        cmd3 = "rm -rf /etc/3rd-party/openldap /var/data/3rd-party/"
        # cmd4 = "docker image prune -a"
        try:
            resp = master_node_obj.execute_cmd(cmd=cmd1)
            LOGGER.debug("resp : %s", resp)
            for worker in worker_node_obj:
                resp = worker.execute_cmd(cmd=cmd2, read_lines=True)
                LOGGER.debug("resp : %s", resp)
                resp = worker.execute_cmd(cmd=cmd3, read_lines=True)
                LOGGER.debug("resp : %s", resp)
                # resp = worker.execute_cmd(cmd=cmd4, read_lines=True)
                # LOGGER.debug("resp : %s", resp)
            return True, resp
        # pylint: disable=broad-except
        except BaseException as error:
            return False, error

    @staticmethod
    # pylint: disable-msg=too-many-locals
    def configure_metallb(node_obj: LogicalNode, data_ip: list, control_ip: list):
        """
        Configure MetalLB on the master node
        param: node_obj : Master node object
        param: data_ip : List of data IPs.
        param: control_ip : List of control IPs.
        return : Boolean
        """
        LOGGER.info("Configuring MetaLB: ")
        metallb_cfg = PROV_CFG['config_metallb']
        LOGGER.info("Enable strict ARP mode")
        resp = node_obj.execute_cmd(cmd=metallb_cfg['check_ARP'], read_lines=True)
        LOGGER.debug("resp: %s", resp)
        resp = node_obj.execute_cmd(cmd=metallb_cfg['enable_strict_ARP'], read_lines=True)
        LOGGER.debug("resp: %s", resp)

        LOGGER.info("Apply manifest")
        resp = node_obj.execute_cmd(cmd=metallb_cfg['apply_manifest_namespace'], read_lines=True)
        LOGGER.debug("resp: %s", resp)
        resp = node_obj.execute_cmd(cmd=metallb_cfg['apply_manifest_metalb'], read_lines=True)
        LOGGER.debug("resp: %s", resp)

        LOGGER.info("Check metallb-system namespace")
        resp = node_obj.execute_cmd(cmd=metallb_cfg['check_namespace'], read_lines=True)
        LOGGER.debug("resp: %s", resp)

        LOGGER.info("Update config file with the provided IPs")
        ip_list = data_ip + control_ip
        filepath = metallb_cfg['config_path']
        with open(filepath) as soln:
            conf = yaml.safe_load(soln)
            new_dict = conf['data']
            ori_value = conf['data']['config']
            temp1 = ori_value.split('addresses:')[1]
            ips = ' \n\t'.join(f'- {each}-{each}' for each in ip_list)
            temp2 = '\n\t' + ips
            new_value = ori_value.replace(temp1, temp2)
            schema = {'config': f'| {new_value}'}
            new_dict.update(schema)
        noalias_dumper = yaml.dumper.SafeDumper
        noalias_dumper.ignore_aliases = lambda self, data: True
        with open(metallb_cfg['new_config_file'], 'w') as soln:
            yaml.dump(conf, soln, default_flow_style=False,
                      sort_keys=False, Dumper=noalias_dumper)

        LOGGER.info("Copy metalLB config file to master node")
        resp = node_obj.copy_file_to_remote(metallb_cfg['new_config_file'],
                                            metallb_cfg['remote_path'])
        if not resp:
            return resp

        LOGGER.info("Apply metalLB config")
        resp = node_obj.execute_cmd(metallb_cfg['apply_config'], read_lines=True)
        LOGGER.debug("resp: %s", resp)

        return True

    @staticmethod
    def post_deployment_steps_lc():
        """
        Perform CSM login, S3 account creation and AWS configuration on client
        returns status boolean
        """
        LOGGER.info("Post Deployment Steps")
        csm_s3 = RestS3user()

        LOGGER.info("Create S3 account")
        csm_s3.create_custom_s3_payload("valid")
        resp = csm_s3.create_s3_account()
        LOGGER.info("Response for account creation: %s", resp.json())
        details = resp.json()
        access_key = details['access_key']
        secret_key = details["secret_key"]
        try:
            LOGGER.info("Configure AWS keys on Client")
            system_utils.execute_cmd(
                common_cmd.CMD_AWS_CONF_KEYS.format(access_key, secret_key))
        except IOError as error:
            LOGGER.error(
                "An error occurred in %s:",
                ProvDeployK8sCortxLib.post_deployment_steps_lc.__name__)
            if isinstance(error.args[0], list):
                LOGGER.error("\n".join(error.args[0]).replace("\\n", "\n"))
            else:
                LOGGER.error(error.args[0])
            return False, error

        return True, "Post Deployment Steps Successful!!"

    # pylint: disable=too-many-arguments
    def write_read_validate_file(self, s3t_obj, bucket_name,
                                 test_file, count, block_size):
        """
        Create test_file with file_size(count*blocksize) and upload to bucket_name
        validate checksum after download and deletes the file
        param:s3t_obj: s3 obj to fetch access_key and secret_key
        param:bucket_name: bucket name
        param: test_file: test file
        param: count: no. of objects
        param block size: block size of the object
        """
        file_path = os.path.join(self.test_dir_path, test_file)
        if not os.path.isdir(self.test_dir_path):
            LOGGER.debug("File path not exists")
            system_utils.execute_cmd(cmd=common_cmd.CMD_MKDIR.format(self.test_dir_path))

        LOGGER.info("Creating a file with name %s", test_file)
        system_utils.create_file(file_path, count, "/dev/urandom", block_size)

        LOGGER.info("Retrieving checksum of file %s", test_file)
        resp1 = system_utils.get_file_checksum(file_path)
        assert_utils.assert_true(resp1[0], resp1[1])
        chksm_before_put_obj = resp1[1]

        LOGGER.info("Uploading a object %s to a bucket %s", test_file, bucket_name)
        resp = s3t_obj.put_object(bucket_name, test_file, file_path)
        assert_utils.assert_true(resp[0], resp[1])

        LOGGER.info("Validate upload of object %s ", test_file)
        resp = s3t_obj.object_list(bucket_name)
        assert_utils.assert_in(test_file, resp[1], f"Failed to upload create {test_file}")

        LOGGER.info("Removing local file from client and downloading object")
        system_utils.remove_file(file_path)
        resp = s3t_obj.object_download(bucket_name, test_file, file_path)
        assert_utils.assert_true(resp[0], resp[1])

        LOGGER.info("Verifying checksum of downloaded file with old file should be same")
        resp = system_utils.get_file_checksum(file_path)
        assert_utils.assert_true(resp[0], resp[1])
        chksm_after_dwnld_obj = resp[1]
        assert_utils.assert_equal(chksm_before_put_obj, chksm_after_dwnld_obj)

        LOGGER.info("Delete the file from the bucket")
        resp = s3t_obj.delete_object(bucket_name, test_file)
        assert_utils.assert_true(resp[0], resp[1])

        LOGGER.info("Delete downloaded file")
        resp = system_utils.remove_file(file_path)
        assert_utils.assert_true(resp[0], resp[1])

    def basic_io_write_read_validate(self, s3t_obj: S3TestLib, bucket_name: str):
        """
        This method write, read and validate the object.
        param: s3t_obj: s3 obj to fetch access_key and secret key
        param: bucket_name:bucket name
        """
        LOGGER.info("STARTED: Basic IO")
        basic_io_config = PROV_CFG["test_basic_io"]

        LOGGER.info("Creating bucket %s", bucket_name)
        resp = s3t_obj.create_bucket(bucket_name)
        assert_utils.assert_true(resp[0], resp[1])

        LOGGER.info("Perform write/read/validate/delete with multiples object sizes. ")
        for b_size, max_count in basic_io_config["io_upper_limits"].items():
            for count in range(0, max_count):
                test_file = "basic_io_" + str(count) + str(b_size)
                block_size = "1M"
                if str(b_size).lower() == "kb":
                    block_size = "1K"

                self.write_read_validate_file(s3t_obj, bucket_name, test_file, count, block_size)

        LOGGER.info("Delete bucket %s", bucket_name)
        resp = s3t_obj.delete_bucket(bucket_name)
        assert_utils.assert_true(resp[0], resp[1])
        LOGGER.info("Basic IO Completed")

    @staticmethod
    def io_workload(access_key, secret_key, bucket_prefix, clients=5):
        """
        S3 bench workload test executed for each of Erasure coding config
        param: access_key: s3 user access key
        param: secret_key: s3 user secret keys
        param: bucket_prefix: bucket prefix
        param: client: no clients request
        """
        LOGGER.info("STARTED: S3 bench workload test")
        workloads = [
            "1Kb", "4Kb", "8Kb", "16Kb", "32Kb", "64Kb", "128Kb", "256Kb", "512Kb",
            "1Mb", "4Mb", "8Mb", "16Mb", "32Mb", "64Mb", "128Mb", "256Mb", "512Mb", "1Gb", "2Gb"
        ]
        resp = s3bench.setup_s3bench()
        assert (resp, resp), "Could not setup s3bench."
        for workload in workloads:
            bucket_name = bucket_prefix + "-" + str(workload).lower()
            if "Kb" in workload:
                samples = 50
            elif "Mb" in workload:
                samples = 10
            else:
                samples = 5
            resp = s3bench.s3bench(access_key, secret_key, bucket=bucket_name,
                                   num_clients=clients,
                                   num_sample=samples, obj_name_pref="test-object-",
                                   obj_size=workload,
                                   skip_cleanup=False, duration=None, log_file_prefix=bucket_prefix)
            LOGGER.info("json_resp %s\n Log Path %s", resp[0], resp[1])
            assert not s3bench.check_log_file_error(resp[1]), \
                f"S3bench workload for object size {workload} failed. " \
                f"Please read log file {resp[1]}"
            LOGGER.info("ENDED: S3 bench workload test")

    @staticmethod
    def dump_in_csv(test_list: List, csv_filepath):
        """
        Method to dump the data in csv file
        param: list of data which needs to be added in row of csv file
        param: csv_filepath: csv file path
        returns: updated csv file with its path
        """
        fields = ['ITERATION', 'POD STATUS']
        with open(csv_filepath, 'w')as fptr:
            # writing the fields
            write = csv.writer(fptr)
            write.writerow(fields)
            for test in test_list:
                write.writerows([test])
            fptr.close()
        return csv_filepath

    @staticmethod
    def get_data_pods(node_obj) -> tuple:
        """
        Get list of data pods in cluster.
        param: node_obj: Master node(Logical Node object)
        return: True/False and pods list/failure message
        """
        LOGGER.info("Get list of data pods in cluster.")
        output = node_obj.execute_cmd(common_cmd.CMD_POD_STATUS +
                                      " -o=custom-columns=NAME:.metadata.name",
                                      read_lines=True)
        data_pod_list = [pod.strip() for pod in output if common_const.POD_NAME_PREFIX in pod]
        if data_pod_list is not None:
            return True, data_pod_list
        return False, "Data PODS are not retrieved for cluster."

    @staticmethod
    def check_pods_status(node_obj) -> bool:
        """
        Helper function to check pods status.
        :param node_obj: Master node(Logical Node object)
        :return: True/False
        """
        LOGGER.info("Checking all Pods are online.")
        resp = node_obj.execute_cmd(cmd=common_cmd.CMD_POD_STATUS, read_lines=True)
        for line in range(1, len(resp)):
            if "Running" not in resp[line]:
                return False
        return True

    # pylint: disable=R0915
    # pylint: disable=too-many-arguments,too-many-locals
    def test_deployment(self, sns_data, sns_parity,
                        sns_spare, dix_data,
                        dix_parity, dix_spare,
                        cvg_count, data_disk_per_cvg, master_node_list,
                        worker_node_list, **kwargs):
        """
        This method is used for deployment with various config on N nodes
        param: sns_data
        param: sns_parity
        param: sns_spare
        param: dix_data
        param: dix_parity
        param: dix_spare
        param: cvg_count
        param: data disk per cvg
        param: master node obj list
        param: worker node obj list
        keyword:setup_k8s_cluster_flag: flag to deploy k8s setup
        keyword:cortx_cluster_deploy: flag to deploy cortx cluster
        keyword:setup_client_config_flag: flsg to setup client with haproxy
        keyword:run_basic_s3_io_flag: flag to run basic s3 io
        keyword:run_s3bench_workload_flag: flag to run s3bench IO
        keyword:destroy_setup_flag:flag to destroy cortx cluster

        """
        setup_k8s_cluster_flag = \
            kwargs.get("setup_k8s_cluster_flag",
                       PROV_CFG['k8s_cortx_deploy']['setup_k8s_cluster_flag'])
        cortx_cluster_deploy_flag = \
            kwargs.get("cortx_cluster_deploy",
                       PROV_CFG['k8s_cortx_deploy']['cortx_cluster_deploy_flag'])
        setup_client_config_flag = \
            kwargs.get("setup_client_config_flag",
                       PROV_CFG['k8s_cortx_deploy']['setup_client_config_flag'])
        run_basic_s3_io_flag = \
            kwargs.get("run_basic_s3_io_flag",
                       PROV_CFG['k8s_cortx_deploy']['run_basic_s3_io_flag'])
        run_s3bench_workload_flag = \
            kwargs.get("run_s3bench_workload_flag",
                       PROV_CFG['k8s_cortx_deploy']['run_s3bench_workload_flag'])
        destroy_setup_flag = kwargs.get("destroy_setup_flag",
                                        PROV_CFG['k8s_cortx_deploy']['destroy_setup_flag'])
        LOGGER.info("STARTED: {%s node (SNS-%s+%s+%s) (DIX-%s+%s+%s) "
                    "k8s based Cortx Deployment", len(worker_node_list),
                    sns_data, sns_parity, sns_spare, dix_data, dix_parity, dix_spare)
        LOGGER.debug("setup_k8s_cluster_flag = %s", setup_k8s_cluster_flag)
        LOGGER.debug("cortx_cluster_deploy_flag = %s", cortx_cluster_deploy_flag)
        LOGGER.debug("setup_client_config_flag = %s", setup_client_config_flag)
        LOGGER.debug("run_basic_s3_io_flag = %s", run_basic_s3_io_flag)
        LOGGER.debug("run_s3bench_workload_flag = %s", run_s3bench_workload_flag)
        LOGGER.debug("destroy_setup_flag = %s", destroy_setup_flag)
        if setup_k8s_cluster_flag:
            LOGGER.info("Step to Perform k8s Cluster Deployment")
            resp = self.setup_k8s_cluster(master_node_list, worker_node_list)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step to Taint master nodes if not already done.")
            for node in master_node_list:
                resp = self.validate_master_tainted(node)
                if not resp:
                    self.taint_master(node)

        if cortx_cluster_deploy_flag:
            LOGGER.info("Step to Download solution file template")
            path = self.checkout_solution_file(self.git_script_tag)
            LOGGER.info("Step to Update solution file template")
            resp = self.update_sol_yaml(worker_obj=worker_node_list, filepath=path,
                                        cortx_image=self.cortx_image,
                                        sns_data=sns_data, sns_parity=sns_parity,
                                        sns_spare=sns_spare, dix_data=dix_data,
                                        dix_parity=dix_parity, dix_spare=dix_spare,
                                        cvg_count=cvg_count, data_disk_per_cvg=data_disk_per_cvg,
                                        size_data_disk="20Gi", size_metadata="20Gi",
                                        glusterfs_size="20Gi")
            assert_utils.assert_true(resp[0], "Failure updating solution.yaml")
            with open(resp[1]) as file:
                LOGGER.info("The solution yaml file is %s\n", file)
            sol_file_path = resp[1]
            system_disk_dict = resp[2]
            LOGGER.info("Step to Perform Cortx Cluster Deployment")
            resp = self.deploy_cortx_cluster(sol_file_path, master_node_list,
                                             worker_node_list, system_disk_dict,
                                             self.docker_username,
                                             self.docker_password, self.git_script_tag)
            assert_utils.assert_true(resp[0], resp[1])
            LOGGER.info("Step to Check s3 server status")
            resp = master_node_list[0].get_pod_name(pod_prefix=common_const.POD_NAME_PREFIX)
            pod_name = resp[1]
            start_time = int(time.time())
            end_time = start_time + 1800  # 30 mins timeout
            while int(time.time()) < end_time:
                resp = self.get_hctl_status(master_node_list[0], pod_name)
                if resp[0]:
                    LOGGER.info("####All the services online. Time Taken : %s",
                                (int(time.time()) - start_time))
                    break
                time.sleep(60)
            assert_utils.assert_true(resp[0], resp[1])

        if setup_client_config_flag:
            resp = system_utils.execute_cmd(common_cmd.CMD_GET_IP_IFACE.format('eth1'))
            eth1_ip = resp[1].strip("'\\n'b'")
            LOGGER.info("Configure HAproxy on client")
            ext_lbconfig_utils.configure_haproxy_lb(master_node_list[0].hostname,
                                                    master_node_list[0].username,
                                                    master_node_list[0].password, eth1_ip,
                                                    PROV_CFG['k8s_cortx_deploy']['pem_file_path'])
            LOGGER.info("Step to Create S3 account and configure credentials")
            resp = self.post_deployment_steps_lc()
            assert_utils.assert_true(resp[0], resp[1])
            access_key, secret_key = S3H_OBJ.get_local_keys()
            s3t_obj = S3TestLib(access_key=access_key, secret_key=secret_key)
            if run_basic_s3_io_flag:
                LOGGER.info("Step to Perform basic IO operations")
                bucket_name = "bucket-" + str(int(time.time()))
                self.basic_io_write_read_validate(s3t_obj, bucket_name)
            if run_s3bench_workload_flag:
                LOGGER.info("Step to Perform S3bench IO")
                bucket_name = "bucket-" + str(int(time.time()))
                self.io_workload(access_key=access_key, secret_key=secret_key,
                                 bucket_prefix=bucket_name)

        if destroy_setup_flag:
            LOGGER.info("Step to Destroy setup")
            resp = self.destroy_setup(master_node_list[0], worker_node_list)
            assert_utils.assert_true(resp[0], resp[1])

        LOGGER.info("ENDED: %s node (SNS-%s+%s+%s) k8s based Cortx Deployment",
                    len(worker_node_list), sns_data, sns_parity, sns_spare)