#!/usr/bin/python
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

"""Pods helper impl. Command builder should not be part of this class.
However validation of sub commands and options can be done in command issuing functions
like send_k8s_cmd.
"""

import logging
import os
import time
from typing import Tuple
from commons import commands
from commons import constants as const
from commons.helpers.host import Host

log = logging.getLogger(__name__)

namespace_map = {}


class LogicalNode(Host):
    """Pods helper class. The Command builder should be written separately and will be
    using this class.
    """

    kube_commands = ('create', 'apply', 'config', 'get', 'explain',
                     'autoscale', 'patch', 'scale', 'exec')

    def get_service_logs(self, svc_name: str, namespace: str, options: '') -> Tuple:
        """Get logs of a pod or service."""
        cmd = commands.FETCH_LOGS.format(svc_name, namespace, options)
        res = self.execute_cmd(cmd)
        return res

    def send_k8s_cmd(
            self,
            operation: str,
            pod: str,
            namespace: str,
            command_suffix: str,
            decode=False,
            **kwargs) -> bytes:
        """send/execute command on logical node/pods."""
        if operation not in LogicalNode.kube_commands:
            raise ValueError(
                "command parameter must be one of %r." % str(LogicalNode.kube_commands))
        log.debug("Performing %s on service %s in namespace %s...", operation, pod, namespace)
        cmd = commands.KUBECTL_CMD.format(operation, pod, namespace, command_suffix)
        resp = self.execute_cmd(cmd, **kwargs)
        if decode:
            resp = (resp.decode("utf8")).strip()
        return resp

    def shutdown_node(self, options=None):
        """Function to shutdown any of the node."""
        try:
            cmd = "shutdown {}".format(options if options else "")
            log.debug(
                "Shutting down %s node using cmd: %s.",
                self.hostname,
                cmd)
            resp = self.execute_cmd(cmd, shell=False)
            log.debug(resp)
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.shutdown_node.__name__, error)
            return False, error

        return True, "Node shutdown successfully"

    def get_pod_name(self, pod_prefix: str = const.POD_NAME_PREFIX):
        """Function to get pod name with given prefix."""
        output = self.execute_cmd(commands.CMD_POD_STATUS +
                                  " -o=custom-columns=NAME:.metadata.name", read_lines=True)
        for lines in output:
            if pod_prefix in lines:
                return True, lines.strip()
        return False, f"pod with prefix \"{pod_prefix}\" not found"

    def send_sync_command(self, pod_prefix):
        """
        Helper function to send sync command to all containers of given pod category
        :param pod_prefix: Prefix to define the pod category
        :return: Bool
        """
        log.info("Run sync command on all containers of pods %s", pod_prefix)
        pod_dict = self.get_all_pods_containers(pod_prefix=pod_prefix)
        if pod_dict:
            for pod, containers in pod_dict.items():
                for cnt in containers:
                    res = self.send_k8s_cmd(
                        operation="exec", pod=pod, namespace=const.NAMESPACE,
                        command_suffix=f"-c {cnt} -- sync", decode=True)
                    log.info("Response for pod %s container %s: %s", pod, cnt, res)

        return True

    def get_all_pods_containers(self, pod_prefix, pod_list=None):
        """
        Helper function to get all pods with containers of given pod_prefix
        :param pod_prefix: Prefix to define the pod category
        :param pod_list: List of pods
        :return: Dict
        """
        pod_containers = {}
        if not pod_list:
            log.info("Get all data pod names of %s", pod_prefix)
            output = self.execute_cmd(commands.CMD_POD_STATUS +
                                      " -o=custom-columns=NAME:.metadata.name", read_lines=True)
            for lines in output:
                if pod_prefix in lines:
                    pod_list.append(lines.strip())

        for pod in pod_list:
            cmd = commands.KUBECTL_GET_POD_CONTAINERS.format(pod)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            output = output[0].split()
            pod_containers[pod] = output

        return pod_containers

    def create_pod_replicas(self, num_replica, deploy=None, pod_name=None):
        """
        Helper function to delete/remove/create pod by changing number of replicas
        :param num_replica: Number of replicas to be scaled
        :param deploy: Name of the deployment of pod
        :param pod_name: Name of the pod
        :return: Bool, string (status, deployment name)
        """
        try:
            if pod_name:
                log.info("Getting deploy and replicaset of pod %s", pod_name)
                resp = self.get_deploy_replicaset(pod_name)
                deploy = resp[1]
            log.info("Scaling %s replicas for deployment %s", num_replica, deploy)
            cmd = commands.KUBECTL_CREATE_REPLICA.format(num_replica, deploy)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            time.sleep(60)
            log.info("Check if pod of deployment %s exists", deploy)
            cmd = commands.KUBECTL_GET_POD_DETAILS.format(deploy)
            output = self.execute_cmd(cmd=cmd, read_lines=True, exc=False)
            status = True if output else False
            return status, deploy
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.create_pod_replicas.__name__, error)
            return False, error

    def delete_pod(self, pod_name, force=False):
        """
        Helper function to delete pod gracefully or forcefully using kubectl delete command
        :param pod_name: Name of the pod
        :param force: Flag to indicate forceful or graceful deletion
        :return: Bool, output
        """
        try:
            log.info("Deleting pod %s", pod_name)
            extra_param = " --grace-period=0 --force" if force else ""
            cmd = commands.K8S_DELETE_POD.format(pod_name) + extra_param
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.delete_pod.__name__, error)
            return False, error

        log.info("Successfully deleted pod %s", pod_name)
        return True, output

    def get_deploy_replicaset(self, pod_name):
        """
        Helper function to get deployment name and replicaset name of the given pod
        :param pod_name: Name of the pod
        :return: Bool, str, str (status, deployment name, replicaset name)
        """
        try:
            log.info("Getting details of pod %s", pod_name)
            cmd = commands.KUBECTL_GET_POD_DETAILS.format(pod_name)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            output = (output[0].split())[-1].split(',')
            deploy = output[0].split('=')[-1]
            replicaset = deploy + "-" + output[-1].split('=')[-1]
            return True, deploy, replicaset
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.get_deploy_replicaset.__name__, error)
            return False, error

    def get_num_replicas(self, replicaset):
        """
        Helper function to get number of desired, current and ready replicas for given replica set
        :param replicaset: Name of the replica set
        :return: Bool, str, str, str (Status, Desired replicas, Current replicas, Ready replicas)
        """
        try:
            log.info("Getting details of replicaset %s", replicaset)
            cmd = commands.KUBECTL_GET_REPLICASET.format(replicaset)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            output = output[0].split()
            log.info("Desired replicas: %s \nCurrent replicas: %s \nReady replicas: %s",
                     output[1], output[2], output[3])
            return True, output[1], output[2], output[3]
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.get_num_replicas.__name__, error)
            return False, error

    def delete_deployment(self, pod_name):
        """
        Helper function to delete deployment of given pod
        :param pod_name: Name of the pod
        :return: Bool, str, str (status, backup path of deployment, deployment name)
        """
        try:
            resp = self.get_deploy_replicaset(pod_name)
            deploy = resp[1]
            log.info("Deployment for pod %s is %s", pod_name, deploy)
            log.info("Taking deployment backup")
            resp = self.backup_deployment(deploy)
            backup_path = resp[1]
            log.info("Deleting deployment %s", pod_name)
            cmd = commands.KUBECTL_DEL_DEPLOY.format(deploy)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            time.sleep(60)
            log.info("Check if pod of deployment %s exists", deploy)
            cmd = commands.KUBECTL_GET_POD_DETAILS.format(deploy)
            output = self.execute_cmd(cmd=cmd, read_lines=True, exc=False)
            status = True if output else False
            return status, backup_path, deploy
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.delete_deployment.__name__, error)
            return False, error

    def recover_deployment_helm(self, deployment_name):
        """
        Helper function to recover the deleted deployment using helm
        :param deployment_name: Name of the deployment to be recovered
        :return: Bool, str, str (status, helm release name, release revision)
        """
        try:
            resp = self.get_helm_rel_name_rev(deployment_name)
            helm_rel = resp[1]
            rel_revision = resp[2]
            log.info("Rolling back the deployment %s using release %s and revision %s",
                     deployment_name, helm_rel, rel_revision)
            cmd = commands.HELM_ROLLBACK.format(helm_rel, rel_revision)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            time.sleep(60)
            log.info("Check if pod of deployment %s exists", deployment_name)
            cmd = commands.KUBECTL_GET_POD_DETAILS.format(deployment_name)
            output = self.execute_cmd(cmd=cmd, read_lines=True, exc=False)
            status = True if output else False
            return status, helm_rel, rel_revision
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.recover_deployment_helm.__name__, error)
            return False, error

    def recover_deployment_k8s(self, backup_path, deployment_name):
        """
        Helper function to recover the deleted deployment using kubectl
        :param deployment_name: Name of the deployment to be recovered
        :param backup_path: Path of the backup taken for given deployment
        :return: Bool, str (status, output)
        """
        try:
            log.info("Recovering deployment using kubectl")
            cmd = commands.KUBECTL_RECOVER_DEPLOY.format(backup_path)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.info("Response: %s", output)
            time.sleep(60)
            log.info("Check if pod of deployment %s exists", deployment_name)
            cmd = commands.KUBECTL_GET_POD_DETAILS.format(deployment_name)
            output = self.execute_cmd(cmd=cmd, read_lines=True, exc=False)
            status = True if output else False
            return status, output
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.recover_deployment_k8s.__name__, error)
            return False, error

    def backup_deployment(self, deployment_name):
        """
        Helper function to take backup of the given deployment
        :param deployment_name: Name of the deployment
        :return: Bool, str (status, path of the backup)
        """
        try:
            filename = deployment_name + "_backup.yaml"
            backup_path = os.path.join("/root", filename)
            log.info("Taking backup for deployment %s", deployment_name)
            cmd = commands.KUBECTL_DEPLOY_BACKUP.format(deployment_name, backup_path)
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            log.debug("Backup for %s is stored at %s", deployment_name, backup_path)
            log.info("Response: %s", output)
            return True, backup_path
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.backup_deployment.__name__, error)
            return False, error

    def get_helm_rel_name_rev(self, deployment_name):
        """
        Helper function to get help release name and revision for given deployment
        :param deployment_name: Name of the deployment
        :return: Bool, str, str (status, helm rel name, helm rel revision)
        """
        try:
            search_str = deployment_name.split('-')[-1]
            log.info("Getting helm release details")
            cmd = commands.HELM_LIST + f" | grep {search_str}"
            output = self.execute_cmd(cmd=cmd, read_lines=True)
            releases = []
            for out in output:
                releases.append(out.split()[0])
            for rel in releases:
                cmd = commands.HELM_GET_VALUES.format(rel)
                output = self.execute_cmd(cmd=cmd, read_lines=True)
                if any(deployment_name in s for s in output):
                    cmd = commands.HELM_HISTORY.format(rel)
                    output = self.execute_cmd(cmd=cmd, read_lines=True)
                    rev = output[-1].split()[0]
                    log.info("Release name: %s\nRevision: %s\n", rel, rev)
                    return True, rel, rev

            log.info("Couldn't find relevant release in helm")
            return False, releases
        except Exception as error:
            log.error("*ERROR* An exception occurred in %s: %s",
                      LogicalNode.get_helm_rel_name_rev.__name__, error)
            return False, error

    def get_all_pods_and_ips(self,pod_prefix):
        """
        Helper function to get pods name with pod_prefix and their IPs
        :param: pod_prefix: Prefix to define the pod category
        :return: dict
        """
        pod_dict = {}
        output = self.execute_cmd(cmd=commands.KUBECTL_GET_POD_IPS,read_lines=True)
        log.debug("output : %s",output)
        for lines in output:
            if pod_prefix in lines:
                data = lines.strip()
                pod_name = data.split()[0]
                pod_ip = data.split()[1].replace("\n","")
                pod_dict[pod_name.strip()] = pod_ip.strip()
        return  pod_dict

    def get_container_of_pod(self,pod_name,container_prefix):
        """
        Helper function to get container with container_prefix from the specified pod_name
        :param: pod_name : Pod name to query container of
        :param: container_prefix: Prefix to define container catergory
        :return: list
        """
        cmd = commands.KUBECTL_GET_POD_CONTAINERS.format(pod_name)
        output = self.execute_cmd(cmd=cmd, read_lines=True)
        output = output[0].split()
        container_list = []
        for each in output:
            if container_prefix in each:
                container_list.append(each)

        return container_list