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

"""Data Integrity test module."""

import os
import logging
from datetime import datetime
import pytest
from libs.s3 import S3H_OBJ
from libs.s3 import S3_CFG
from libs.s3.s3_test_lib import S3TestLib
from libs.s3.s3_multipart_test_lib import S3MultipartTestLib
from libs.di import di_lib
from config import CMN_CFG
from commons.ct_fail_on import CTFailOn
from commons.errorcodes import error_handler
from commons.params import TEST_DATA_PATH
from commons.utils import system_utils as sys_util
from commons.constants import const


class TestDIWithChangingS3Params:

    """Data Integrity Test suite."""

    @classmethod
    def setup_class(cls):
        """
        Function will be invoked prior to each test case.

        It will perform all prerequisite test suite steps if any.
        """
        cls.log = logging.getLogger(__name__)
        cls.log.info("STARTED: setup test suite operations.")
        cls.s3obj = S3TestLib()
        cls.s3_mp_test_obj = S3MultipartTestLib(endpoint_url=S3_CFG["s3_url"])
        cls.obj_name_1 = "di-test-obj-1-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.obj_name_2 = "di-test-obj-2-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.obj_name_3 = "di-test-obj-3-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.bucket_name_1 = "di-test-bkt-1-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.bucket_name_2 = "di-test-bkt-2-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.bucket_name_3 = "di-test-bkt-3-{}".format(datetime.utcnow().strftime('%Y%m%d%H%M%S'))
        cls.WRITE_PARAM = "S3_WRITE_DATA_INTEGRITY_CHECK"
        cls.READ_PARAM = "S3_READ_DATA_INTEGRITY_CHECK"
        cls.params = dict()
        cls.test_dir_path = os.path.join(TEST_DATA_PATH, "TestDI")
        if not sys_util.path_exists(cls.test_dir_path):
            resp = sys_util.make_dirs(cls.test_dir_path)
            cls.log.info("Created path: %s", resp)
        cls.LOCAL_PATH = cls.test_dir_path + "/s3config.yaml"
        cls.F_PATH = cls.test_dir_path + "/temp.txt"
        cls.F_PATH_COPY = cls.test_dir_path + "/temp-copy.txt"
        cls.log.info("ENDED: setup test suite operations.")

    def setup_method(self):
        """
        Function will be invoked before test execution.

        It will perform prerequisite test steps if any
        """
        self.log.info("STARTED: Setup operations")
        self.log.info("Getting s3 server config file")
        self.get_config_file()
        self.log.info("Saving s3config file to %s", self.LOCAL_PATH)
        self.log.info("ENDED: Setup operations")

    def teardown_method(self):
        """
        This function will be invoked after each test case.
        It will perform all cleanup operations.
        This function will delete buckets and accounts and files created for tests.
        """
        self.log.info("STARTED: Teardown operations.")
        self.put_config_file_restart_server()
        self.log.info("ENDED: Teardown operations.")

    @classmethod
    def teardown_class(cls):
        """
        teardown class
        """
        cls.log.info("STARTED: Teardown cls operations.")
        cls.log.info("Deleting a backup file and directory...")
        if sys_util.path_exists(cls.LOCAL_PATH):
            sys_util.remove_file(cls.LOCAL_PATH)
        if sys_util.path_exists(cls.F_PATH):
            sys_util.remove_file(cls.F_PATH)
        if sys_util.path_exists(cls.F_PATH_COPY):
            sys_util.remove_file(cls.F_PATH_COPY)
        if sys_util.path_exists(cls.test_dir_path):
            sys_util.remove_dirs(cls.test_dir_path)
        cls.log.info("Deleted a backup file and directory")
        cls.log.info("ENDED: Teardown class operations.")

    def get_config_file(self):
        """
        this will get config file and store in temp location
        """
        S3H_OBJ.copy_s3server_file(file_path=const.S3_CONFIG, local_path=self.LOCAL_PATH)

    def put_config_file_restart_server(self):
        """
        this will put config file and
        restart s3 server
        """
        nodes = CMN_CFG["nodes"]
        for node in nodes:
            di_lib.copy_local_to_s3_config(backup_path=self.LOCAL_PATH, host=node['hostname'],
                                           user=node['username'],
                                           password=node['password'])
            S3H_OBJ.restart_s3server_processes(host=node['hostname'],
                                               user=node['username'],
                                               pwd=node['password'])
        self.log.info("updated config to default and restarted s3 server")

    def update_s3config_and_restart_s3_server(self, params):
        """
        this will accept params and values
        update s3 config files and restarts s3 server
        """
        nodes = CMN_CFG["nodes"]
        for node in nodes:
            S3H_OBJ.update_s3config(parameter=self.WRITE_PARAM, value=params[self.WRITE_PARAM],
                                    host=node['hostname'], user=node['username'],
                                    password=node['password'])
            S3H_OBJ.update_s3config(parameter=self.READ_PARAM, value=params[self.READ_PARAM],
                                    host=node['hostname'], user=node['username'],
                                    password=node['password'])
            S3H_OBJ.restart_s3server_processes()

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29273')
    @CTFailOn(error_handler)
    def test_29273(self):
        """
        this will test normal file upload
        with DI flag ON for both write and read
        """
        self.log.info("Step 1::: Setting up params and restarting server")
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = True
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        file_size = [1, 2, 3, 4, 5]
        result = True
        for size in file_size:
            self.log.info("creating a file of size %s MB", size)
            sys_util.create_file(fpath=self.F_PATH, count=size)
            self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                  file_path=self.F_PATH)
            self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                       obj_name=self.obj_name_1, file_path=self.F_PATH_COPY)
            self.s3obj.delete_object(bucket_name=self.bucket_name_1,obj_name=self.obj_name_1)
            result = sys_util.validate_checksum(file_path_1=self.F_PATH,
                                                file_path_2=self.F_PATH_COPY)
            if not result:
                break
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.log.info("Step 2::: Calculating checksum")
        if result:
            assert True
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29276')
    @CTFailOn(error_handler)
    def test_29276(self):
        """
        this will test copy object to same bucket with diff name
        with DI disabled
        """
        self.log.info("Step 1::: Setting up params and restarting server")
        self.params[self.WRITE_PARAM] = False
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.log.info("Step 2::: Creating file and bucket")
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        sys_util.create_file(fpath=self.F_PATH, count=1)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_1,
                                         dest_object=self.obj_name_2)
        self.log.info(resp_cp)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.log.info("Step 3::: Comparing ETags")
        if resp[1]['ETag'] == resp_cp[1]['CopyObjectResult']['ETag']:
            assert True
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29277')
    @CTFailOn(error_handler)
    def test_29277(self):
        """
        this will test copy object to same bucket with diff name
        with DI enabled
        """
        self.log.info("Step 1::: Setting up params and restarting server")
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = True
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.log.info("Step 2::: Creating file and bucket")
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        sys_util.create_file(fpath=self.F_PATH, count=1)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_1,
                                         dest_object=self.obj_name_2)
        self.log.info(resp_cp)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.log.info("Step 3::: Comparing ETags")
        if resp[1]['ETag'] == resp_cp[1]['CopyObjectResult']['ETag']:
            assert False
        else:
            assert True

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29281')
    @CTFailOn(error_handler)
    def test_29281(self):
        """
        Test to verify copy object to different bucket with same
        object name with Data Integrity disabled.
        """
        self.params[self.WRITE_PARAM] = False
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_2)
        sys_util.create_file(fpath=self.F_PATH, count=1)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_2,
                                         dest_object=self.obj_name_1)
        self.log.info(resp_cp)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.s3obj.delete_bucket(self.bucket_name_2, force=True)
        if resp[1]['ETag'] == resp_cp[1]['CopyObjectResult']['ETag']:
            assert True
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29282')
    @CTFailOn(error_handler)
    def test_29282(self):
        """
        Test to verify copy of copied object using simple object upload with
        Data Integrity flag ON for write and OFF for read
        """
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_2)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_3)
        sys_util.create_file(fpath=self.F_PATH, count=1)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_2,
                                         dest_object=self.obj_name_2)
        self.log.info(resp_cp)
        resp_cp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_2,
                                            source_object=self.obj_name_2,
                                            dest_bucket=self.bucket_name_3,
                                            dest_object=self.obj_name_3)
        self.s3obj.object_download(bucket_name=self.bucket_name_3,
                                   obj_name=self.obj_name_3, file_path=self.F_PATH_COPY)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH, file_path_2=self.F_PATH_COPY)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.s3obj.delete_bucket(self.bucket_name_2, force=True)
        self.s3obj.delete_bucket(self.bucket_name_3, force=True)
        if result:
            if resp_cp[1]['CopyObjectResult']['ETag'] == resp_cp_cp[1]['CopyObjectResult']['ETag']:
                assert True
            else:
                assert False
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29284')
    @CTFailOn(error_handler)
    def test_29284(self):
        """
        Test to verify copy object with chunk upload and
        GET operation with range read with file size 50mb
        with Data Integrity flag ON for write and OFF for read
        """
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_2)
        sys_util.create_file(fpath=self.F_PATH, count=50)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        self.s3obj.copy_object(source_bucket=self.bucket_name_1, source_object=self.obj_name_1,
                               dest_bucket=self.bucket_name_2, dest_object=self.obj_name_2)
        resp = self.s3_mp_test_obj.get_byte_range_of_object(bucket_name=self.bucket_name_2,
                                                            my_key=self.obj_name_2,
                                                            start_byte=8888, stop_byte=9999)
        resp_full = self.s3obj.object_download(bucket_name=self.bucket_name_2,
                                               obj_name=self.obj_name_2,
                                               file_path=self.F_PATH_COPY)
        self.log.info(resp)
        self.log.info(resp_full)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH, file_path_2=self.F_PATH_COPY)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.s3obj.delete_bucket(self.bucket_name_2, force=True)
        if result:
            assert True
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29286')
    @CTFailOn(error_handler)
    def test_29286(self):
        """
        Test to overwrite an object using copy object api with
        Data Integrity flag ON for write and OFF for read
        """
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_2)
        sys_util.create_file(fpath=self.F_PATH, count=50)
        resp = self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                                     file_path=self.F_PATH)
        self.log.info(resp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_2,
                                         dest_object=self.obj_name_1)
        self.log.info(resp_cp)
        resp_cp = self.s3obj.copy_object(source_bucket=self.bucket_name_2,
                                         source_object=self.obj_name_1,
                                         dest_bucket=self.bucket_name_1,
                                         dest_object=self.obj_name_1)
        self.log.info(resp_cp)
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH_COPY)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH, file_path_2=self.F_PATH_COPY)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        self.s3obj.delete_bucket(self.bucket_name_2, force=True)
        if result:
            assert True
        else:
            assert False

    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29288')
    @CTFailOn(error_handler)
    def test_29288(self):
        """
        Test to verify multipart upload with s3server restart after every upload
        with Data Integrity flag ON for write and OFF for read
        """
        parts = list()
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        res_sp_file = sys_util.split_file(filename=self.F_PATH, size=25,
                                          split_count=5, random_part_size=False)
        self.log.info(res_sp_file)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        res = self.s3_mp_test_obj.create_multipart_upload(self.bucket_name_1,
                                                          self.obj_name_1)
        mpu_id = res[1]["UploadId"]
        self.log.info("Multipart Upload initiated with mpu_id %s", mpu_id)
        self.log.info("Uploading parts into bucket")
        i = 0
        while i < 5:
            with open(res_sp_file[i]["Output"], "rb") as file_pointer:
                data = file_pointer.read()
            resp = self.s3_mp_test_obj.upload_part(body=data,
                                                   bucket_name=self.bucket_name_1,
                                                   object_name=self.obj_name_1,
                                                   upload_id=mpu_id, part_number=i+1)
            parts.append({"PartNumber": i+1, "ETag": resp[1]["ETag"]})
            S3H_OBJ.restart_s3server_processes()
            i += 1
        resp_cu = self.s3_mp_test_obj.complete_multipart_upload(mpu_id=mpu_id,
                                                                parts=parts,
                                                                bucket=self.bucket_name_1,
                                                                object_name=self.obj_name_1)
        self.log.info(resp_cu)
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH_COPY)
        self.s3obj.delete_bucket(self.bucket_name_1, force=True)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH, file_path_2=self.F_PATH_COPY)
        if result:
            assert True
        else:
            assert False

    @pytest.mark.skip(reason="Not tested, hence marking skip.")
    @pytest.mark.data_integrity
    @pytest.mark.tags('TEST-29289')
    @CTFailOn(error_handler)
    def test_29289(self):
        """
        Test to verify Fault Injection with different modes
        using simple object upload with Data Integrity
        flag ON for write and OFF for read
        """
        self.params[self.WRITE_PARAM] = True
        self.params[self.READ_PARAM] = False
        self.update_s3config_and_restart_s3_server(params=self.params)
        first_byte_for_write = ['f', 'z', 'k']
        first_byte_for_read = ['F', 'Z', 'K']
        # todo change server config introduce data corruption
        # buff, csum = data_gen.generate(size=1024 * 1024 * 5, seed=data_gen.get_random_seed())
        # buff[0] = ord(first_byte_for_write[0])
        # data_gen.save_buf_to_file(fbuf=buff, csum=csum, size=1024 * 1024, name="test_di")
        sys_util.create_file(fpath=self.F_PATH, count=1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_1)
        self.s3obj.create_bucket(bucket_name=self.bucket_name_2)
        # test scene 1
        with open(self.F_PATH, 'rb+') as f_random:
            data = f_random.read()
            byt_ary = bytearray(data)
            byt_ary[0] = ord(first_byte_for_write[0])
            data = bytes(byt_ary)
            f_random.write(data)
        self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                              file_path=self.F_PATH)
        self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                               source_object=self.obj_name_1,
                               dest_bucket=self.bucket_name_2,
                               dest_object=self.obj_name_2)
        # this copy operation should fail
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH)
        # this get operation should fail
        # test scene 2
        with open(self.F_PATH, 'rb+') as f_random:
            data = f_random.read()
            byt_ary = bytearray(data)
            byt_ary[0] = ord(first_byte_for_write[1])
            data = bytes(byt_ary)
            f_random.write(data)
        self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                              file_path=self.F_PATH)
        self.s3obj.copy_object(source_bucket=self.bucket_name_1,
                               source_object=self.obj_name_1,
                               dest_bucket=self.bucket_name_2,
                               dest_object=self.obj_name_2)
        # this copy operation should fail
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH)
        # this get operation should fail
        with open(self.F_PATH, 'rb+') as f_random:
            data = f_random.read()
            byt_ary = bytearray(data)
            byt_ary[0] = ord(first_byte_for_write[2])
            data = bytes(byt_ary)
            f_random.write(data)
        self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                              file_path=self.F_PATH)
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH_COPY)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH,
                                            file_path_2=self.F_PATH_COPY)
        if result:
            assert True
        else:
            assert False
        # ETAG should match
        # test scene 3
        with open(self.F_PATH, 'rb+') as f_random:
            data = f_random.read()
            byt_ary = bytearray(data)
            byt_ary[0] = ord(first_byte_for_read[2])
            data = bytes(byt_ary)
            f_random.write(data)
        self.s3obj.put_object(bucket_name=self.bucket_name_1, object_name=self.obj_name_1,
                              file_path=self.F_PATH)
        self.s3obj.object_download(bucket_name=self.bucket_name_1,
                                   obj_name=self.obj_name_1, file_path=self.F_PATH_COPY)
        result = sys_util.validate_checksum(file_path_1=self.F_PATH,
                                            file_path_2=self.F_PATH_COPY)
        # checksum should match
        if result:
            assert True
        else:
            assert False