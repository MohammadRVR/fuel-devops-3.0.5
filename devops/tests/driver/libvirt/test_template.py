#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import os

import mock
from netaddr import IPAddress
import yaml

from devops.error import DevopsObjNotFound
from devops.models import AddressPool
from devops.models import Environment
from devops.models import Group
from devops.tests.driver.libvirt.base import LibvirtTestCase


ENV_TMPLT = """
---
aliases:

  dynamic_address_pool:
   - &pool_default 10.109.0.0/16:24

  default_interface_model:
   - &interface_model e1000

template:
  devops_settings:
    env_name: test_env

    address_pools:
    # Network pools used by the environment
      fuelweb_admin-pool01:
        net: *pool_default
        params:
          tag: 0
          ip_reserved:
            gateway: +1
            l2_network_device: +1
          ip_ranges:
            default: [+2, -2]
            dhcp: [+2, -2]
      public-pool01:
        net: *pool_default
        params:
          tag: 0
          ip_reserved:
            gateway: +1
            l2_network_device: +1
          ip_ranges:
            default: [+2, +127]
            floating: [+128, -2]
      storage-pool01:
        net: *pool_default
        params:
          tag: 101
          ip_reserved:
            l2_network_device: +1
      management-pool01:
        net: *pool_default
        params:
          tag: 102
          ip_reserved:
            l2_network_device: +1
      private-pool01:
        net: *pool_default
        params:
          tag: 103
          ip_reserved:
            l2_network_device: +1

    groups:
     - name: rack-01
       driver:
         name: devops.driver.libvirt
         params:
           connection_string: test:///default
           storage_pool_name: default-pool
           stp: True
           hpet: False
           use_host_cpu: true

       network_pools:  # Address pools for OpenStack networks.
         # Actual names should be used for keys
         # (the same as in Nailgun, for example)

         fuelweb_admin: fuelweb_admin-pool01
         public: public-pool01
         storage: storage-pool01
         management: management-pool01
         private: private-pool01

       l2_network_devices:  # Libvirt bridges. It is *NOT* Nailgun networks
         admin:
           address_pool: fuelweb_admin-pool01
           dhcp: false
           forward:
             mode: nat

         public:
           address_pool: public-pool01
           dhcp: false
           forward:
             mode: nat

         storage:
           address_pool: storage-pool01
           dhcp: false

         management:
           address_pool: management-pool01
           dhcp: false

         private:
           address_pool: private-pool01
           dhcp: false

       nodes:
        - name: admin        # Custom name of VM for Fuel admin node
          role: fuel_main  # Fixed role for Fuel main node properties
          params:
            vcpu: 2
            memory: 3072
            hypervisor: test
            architecture: i686
            boot:
              - hd
              - cdrom
            volumes:
             - name: system
               capacity: 10
               format: qcow2
             - name: iso
               source_image: /tmp/admin.iso
               format: raw
               device: cdrom
               bus: ide        # for boot from usb - 'usb'
            interfaces:
             - label: eth0
               l2_network_device: admin
               interface_model: *interface_model
            network_config:
              eth0:
                networks:
                 - fuelweb_admin

          # Subordinate nodes

        - name: subordinate-01
          role: fuel_subordinate
          params:  &rack-01-subordinate-node-params
            vcpu: 2
            memory: 3072
            hypervisor: test
            architecture: i686
            boot:
              - network
              - hd
            volumes:
             - name: system
               capacity: 10
               format: qcow2
             - name: cinder
               capacity: 10
               format: qcow2
             - name: swift
               capacity: 10
               format: qcow2

            # List of node interfaces
            interfaces:
             - label: eth0
               l2_network_device: admin
               interface_model: *interface_model
             - label: eth1
               l2_network_device: public
               interface_model: *interface_model
             - label: eth2
               l2_network_device: storage
               interface_model: *interface_model
             - label: eth3
               l2_network_device: management
               interface_model: *interface_model
             - label: eth4
               l2_network_device: private
               interface_model: *interface_model
               features: ['sriov']

            # How Nailgun/OpenStack networks should assigned for interfaces
            network_config:
              eth0:
                networks:
                 - fuelweb_admin  # Nailgun/OpenStack network name
              eth1:
                networks:
                 - public
              eth2:
                networks:
                 - storage
              eth3:
                networks:
                 - management
              eth4:
                networks:
                 - private


        - name: subordinate-02
          role: fuel_subordinate
          params: *rack-01-subordinate-node-params
"""


class TestLibvirtTemplate(LibvirtTestCase):

    def setUp(self):
        super(TestLibvirtTemplate, self).setUp()

        # speed up retry
        self.sleep_mock = self.patch('time.sleep')

        # mock open
        self.open_mock = mock.mock_open(read_data='image_data')
        self.patch('devops.driver.libvirt.libvirt_driver.open',
                   self.open_mock, create=True)

        self.os_mock = self.patch('devops.helpers.helpers.os')
        self.os_mock.urandom = os.urandom
        # noinspection PyPep8Naming
        Size = collections.namedtuple('Size', ['st_size'])
        self.file_sizes = {
            '/tmp/admin.iso': Size(st_size=500),
        }
        self.os_mock.stat.side_effect = self.file_sizes.get

        # Create Environment
        self.full_conf = yaml.load(ENV_TMPLT)
        self.env = Environment.create_environment(self.full_conf)

        self.d = self.env.get_group(name='rack-01').driver

    def test_ips(self):
        admin_net = AddressPool.objects.get(
            name='fuelweb_admin-pool01').ip_network
        pub_net = AddressPool.objects.get(
            name='public-pool01').ip_network
        stor_net = AddressPool.objects.get(
            name='storage-pool01').ip_network
        mng_net = AddressPool.objects.get(
            name='management-pool01').ip_network
        priv_net = AddressPool.objects.get(
            name='private-pool01').ip_network

        def assert_ip_in_net(ip, net):
            assert IPAddress(ip) in net

        admin_node = self.env.get_node(name='admin')
        adm_eth0 = admin_node.interface_set.get(label='eth0')
        assert len(adm_eth0.addresses) == 1
        assert_ip_in_net(adm_eth0.addresses[0].ip_address, admin_net)

        for node_name in ('subordinate-01', 'subordinate-02'):
            subordinate_node = self.env.get_node(name=node_name)
            subordinate_eth0 = subordinate_node.interface_set.get(label='eth0')
            assert len(subordinate_eth0.addresses) == 1
            assert_ip_in_net(subordinate_eth0.addresses[0].ip_address, admin_net)
            subordinate_eth2 = subordinate_node.interface_set.get(label='eth1')
            assert len(subordinate_eth2.addresses) == 1
            assert_ip_in_net(subordinate_eth2.addresses[0].ip_address, pub_net)
            subordinate_eth2 = subordinate_node.interface_set.get(label='eth2')
            assert len(subordinate_eth2.addresses) == 1
            assert_ip_in_net(subordinate_eth2.addresses[0].ip_address, stor_net)
            subordinate_eth3 = subordinate_node.interface_set.get(label='eth3')
            assert len(subordinate_eth3.addresses) == 1
            assert_ip_in_net(subordinate_eth3.addresses[0].ip_address, mng_net)
            subordinate_eth4 = subordinate_node.interface_set.get(label='eth4')
            assert len(subordinate_eth4.addresses) == 1
            assert_ip_in_net(subordinate_eth4.addresses[0].ip_address, priv_net)

    def test_db(self):
        # groups
        assert len(self.env.group_set.all()) == 1
        group = self.env.get_group(name='rack-01')
        assert group

        # address polls
        assert len(self.env.addresspool_set.all()) == 5
        get_ap = self.env.get_address_pool
        assert get_ap(name='fuelweb_admin-pool01')
        assert get_ap(name='fuelweb_admin-pool01').tag == 0
        assert get_ap(name='public-pool01')
        assert get_ap(name='public-pool01').tag == 0
        assert get_ap(name='storage-pool01')
        assert get_ap(name='storage-pool01').tag == 101
        assert get_ap(name='management-pool01')
        assert get_ap(name='management-pool01').tag == 102
        assert get_ap(name='private-pool01')
        assert get_ap(name='private-pool01').tag == 103

        # l2 network devices
        get_l2nd = group.get_l2_network_device
        assert get_l2nd(name='admin')
        assert get_l2nd(name='admin').forward.mode == 'nat'
        assert get_l2nd(name='admin').dhcp is False
        assert get_l2nd(name='public')
        assert get_l2nd(name='public').forward.mode == 'nat'
        assert get_l2nd(name='public').dhcp is False
        assert get_l2nd(name='storage')
        assert get_l2nd(name='storage').forward.mode is None
        assert get_l2nd(name='storage').dhcp is False
        assert get_l2nd(name='management')
        assert get_l2nd(name='management').forward.mode is None
        assert get_l2nd(name='management').dhcp is False
        assert get_l2nd(name='private')
        assert get_l2nd(name='private').forward.mode is None
        assert get_l2nd(name='private').dhcp is False

        assert len(self.env.get_nodes()) == 3

        # admin node
        admin_node = self.env.get_node(name='admin')
        assert admin_node.role == 'fuel_main'
        assert admin_node.vcpu == 2
        assert admin_node.memory == 3072
        assert admin_node.hypervisor == 'test'
        assert admin_node.architecture == 'i686'
        assert admin_node.boot == ['hd', 'cdrom']
        adm_sys_vol = admin_node.get_volume(name='system')
        assert adm_sys_vol.capacity == 10
        assert adm_sys_vol.format == 'qcow2'
        adm_sys_disk = admin_node.diskdevice_set.get(volume=adm_sys_vol)
        assert adm_sys_disk.device == 'disk'
        assert adm_sys_disk.bus == 'virtio'
        assert adm_sys_disk.target_dev == 'sda'
        adm_iso_vol = admin_node.get_volume(name='iso')
        assert adm_iso_vol.capacity is None
        assert adm_iso_vol.source_image == '/tmp/admin.iso'
        assert adm_iso_vol.format == 'raw'
        adm_iso_disk = admin_node.diskdevice_set.get(volume=adm_iso_vol)
        assert adm_iso_disk.device == 'cdrom'
        assert adm_iso_disk.bus == 'ide'
        assert adm_iso_disk.target_dev == 'sdb'
        adm_eth0 = admin_node.interface_set.get(label='eth0')
        assert adm_eth0.label == 'eth0'
        assert adm_eth0.model == 'e1000'
        assert adm_eth0.l2_network_device.name == 'admin'
        adm_nc = admin_node.networkconfig_set.get(label='eth0')
        assert adm_nc.label == 'eth0'
        assert adm_nc.networks == ['fuelweb_admin']
        assert adm_nc.parents == []
        assert adm_nc.aggregation is None

        # subordinate nodes
        for subordinate_name in ('subordinate-01', 'subordinate-02'):
            subordinate_node = self.env.get_node(name=subordinate_name)
            assert subordinate_node.role == 'fuel_subordinate'
            assert subordinate_node.vcpu == 2
            assert subordinate_node.memory == 3072
            assert subordinate_node.hypervisor == 'test'
            assert subordinate_node.architecture == 'i686'
            assert subordinate_node.boot == ['network', 'hd']

            # Volumes and Disks
            subordinate_sys_vol = subordinate_node.get_volume(name='system')
            assert subordinate_sys_vol
            assert subordinate_sys_vol.capacity == 10
            assert subordinate_sys_vol.format == 'qcow2'
            subordinate_sys_disk = subordinate_node.diskdevice_set.get(
                volume=subordinate_sys_vol)
            assert subordinate_sys_disk.device == 'disk'
            assert subordinate_sys_disk.bus == 'virtio'
            assert subordinate_sys_disk.target_dev == 'sda'
            subordinate_cinder_vol = subordinate_node.get_volume(name='cinder')
            assert subordinate_cinder_vol
            assert subordinate_cinder_vol.capacity == 10
            assert subordinate_cinder_vol.format == 'qcow2'
            subordinate_cinder_disk = subordinate_node.diskdevice_set.get(
                volume=subordinate_cinder_vol)
            assert subordinate_cinder_disk.device == 'disk'
            assert subordinate_cinder_disk.bus == 'virtio'
            assert subordinate_cinder_disk.target_dev == 'sdb'
            subordinate_swift_vol = subordinate_node.get_volume(name='swift')
            assert subordinate_swift_vol
            assert subordinate_swift_vol.capacity == 10
            assert subordinate_swift_vol.format == 'qcow2'
            subordinate_swift_disk = subordinate_node.diskdevice_set.get(
                volume=subordinate_swift_vol)
            assert subordinate_swift_disk.device == 'disk'
            assert subordinate_swift_disk.bus == 'virtio'
            assert subordinate_swift_disk.target_dev == 'sdc'

            # Interfaces
            subordinate_eth0 = subordinate_node.interface_set.get(label='eth0')
            assert subordinate_eth0
            assert subordinate_eth0.label == 'eth0'
            assert subordinate_eth0.model == 'e1000'
            assert subordinate_eth0.l2_network_device.name == 'admin'
            assert subordinate_eth0.features == []
            subordinate_eth1 = subordinate_node.interface_set.get(label='eth1')
            assert subordinate_eth1
            assert subordinate_eth1.label == 'eth1'
            assert subordinate_eth1.model == 'e1000'
            assert subordinate_eth1.l2_network_device.name == 'public'
            assert subordinate_eth1.features == []
            subordinate_eth2 = subordinate_node.interface_set.get(label='eth2')
            assert subordinate_eth2
            assert subordinate_eth2.label == 'eth2'
            assert subordinate_eth2.model == 'e1000'
            assert subordinate_eth2.l2_network_device.name == 'storage'
            assert subordinate_eth2.features == []
            subordinate_eth3 = subordinate_node.interface_set.get(label='eth3')
            assert subordinate_eth3
            assert subordinate_eth3.label == 'eth3'
            assert subordinate_eth3.model == 'e1000'
            assert subordinate_eth3.l2_network_device.name == 'management'
            assert subordinate_eth3.features == []
            subordinate_eth4 = subordinate_node.interface_set.get(label='eth4')
            assert subordinate_eth4
            assert subordinate_eth4.label == 'eth4'
            assert subordinate_eth4.model == 'e1000'
            assert subordinate_eth4.features == ['sriov']

            # Network Configs
            assert subordinate_eth4.l2_network_device.name == 'private'
            subordinate_eth0_nc = subordinate_node.networkconfig_set.get(label='eth0')
            assert subordinate_eth0_nc
            assert subordinate_eth0_nc.label == 'eth0'
            assert subordinate_eth0_nc.networks == ['fuelweb_admin']
            assert subordinate_eth0_nc.parents == []
            assert subordinate_eth0_nc.aggregation is None
            subordinate_eth1_nc = subordinate_node.networkconfig_set.get(label='eth1')
            assert subordinate_eth1_nc
            assert subordinate_eth1_nc.label == 'eth1'
            assert subordinate_eth1_nc.networks == ['public']
            assert subordinate_eth1_nc.parents == []
            assert subordinate_eth1_nc.aggregation is None
            subordinate_eth2_nc = subordinate_node.networkconfig_set.get(label='eth2')
            assert subordinate_eth2_nc
            assert subordinate_eth2_nc.label == 'eth2'
            assert subordinate_eth2_nc.networks == ['storage']
            assert subordinate_eth2_nc.parents == []
            assert subordinate_eth2_nc.aggregation is None
            subordinate_eth3_nc = subordinate_node.networkconfig_set.get(label='eth3')
            assert subordinate_eth3_nc
            assert subordinate_eth3_nc.label == 'eth3'
            assert subordinate_eth3_nc.networks == ['management']
            assert subordinate_eth3_nc.parents == []
            assert subordinate_eth3_nc.aggregation is None
            subordinate_eth4_nc = subordinate_node.networkconfig_set.get(label='eth4')
            assert subordinate_eth4_nc
            assert subordinate_eth4_nc.label == 'eth4'
            assert subordinate_eth4_nc.networks == ['private']
            assert subordinate_eth4_nc.parents == []
            assert subordinate_eth4_nc.aggregation is None

    def test_life_cycle(self):
        assert len(self.d.get_allocated_networks()) == 0
        assert len(self.d.conn.listDefinedNetworks()) == 0
        assert len(self.d.conn.listDefinedDomains()) == 0

        self.env.define()

        # pylint: disable=map-builtin-not-iterating
        nets = map(str, self.d.get_allocated_networks())
        # pylint: enable=map-builtin-not-iterating
        assert sorted(nets) == [
            '10.109.0.1/24',
            '10.109.1.1/24',
            '10.109.2.1/24',
            '10.109.3.1/24',
            '10.109.4.1/24',
        ]

        assert sorted(self.d.conn.listDefinedNetworks()) == [
            'test_env_admin',
            'test_env_management',
            'test_env_private',
            'test_env_public',
            'test_env_storage',
        ]

        assert sorted(self.d.conn.listDefinedDomains()) == [
            'test_env_admin',
            'test_env_subordinate-01',
            'test_env_subordinate-02',
        ]

        self.env.start()

        networks = self.d.conn.listAllNetworks()

        assert len(networks) == 5
        for network in networks:
            assert network.isActive()

        domains = self.d.conn.listAllDomains()
        assert len(domains) == 3
        for domain in domains:
            assert domain.isActive()

        self.env.destroy()

        networks = self.d.conn.listAllNetworks()
        assert len(networks) == 5
        for network in networks:
            assert network.isActive()  # should be active

        domains = self.d.conn.listAllDomains()
        assert len(domains) == 3
        for domain in domains:
            assert not domain.isActive()

        self.env.erase()

        assert len(self.d.get_allocated_networks()) == 0
        assert len(self.d.conn.listAllNetworks()) == 0
        assert len(self.d.conn.listAllDomains()) == 0

    def test_object_not_found_exceptions(self):
        with self.assertRaises(DevopsObjNotFound):
            Environment.get(name='other-env')

        with self.assertRaises(DevopsObjNotFound):
            self.env.get_group(name='other-rack')

        with self.assertRaises(DevopsObjNotFound):
            self.env.get_address_pool(name='other-pool')

        with self.assertRaises(DevopsObjNotFound):
            self.env.get_node(name='other-node')

        group = self.env.get_group(name='rack-01')

        with self.assertRaises(DevopsObjNotFound):
            group.get_l2_network_device(name='other-device')

        with self.assertRaises(DevopsObjNotFound):
            group.get_network_pool(name='other-pool')

        with self.assertRaises(DevopsObjNotFound):
            group.get_node(name='other-node')

        with self.assertRaises(DevopsObjNotFound):
            Group.get(name='other-group')

        node = self.env.get_node(name='subordinate-01')

        with self.assertRaises(DevopsObjNotFound):
            node.get_volume(name='other-volume')
