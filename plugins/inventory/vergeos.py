# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
name: vergeos
short_description: VergeOS dynamic inventory plugin
version_added: "1.0.0"
author:
  - VergeIO (@vergeio)
description:
  - Builds Ansible inventory from VergeOS platform.
  - Queries VMs, networks, snapshots, and other resources via the VergeOS API.
  - Automatically creates groups based on tenants, clusters, power states, and custom labels.
  - Supports filtering by tenant, cluster, or other criteria.
  - Provides comprehensive host variables for all discovered resources.
requirements:
  - python >= 3.9
  - pyvergeos
extends_documentation_fragment:
  - constructed
  - inventory_cache
options:
  plugin:
    description: Token that ensures this is a source file for the plugin.
    required: true
    type: str
    choices: ['vergeos', 'vergeio.vergeos.vergeos']
  host:
    description:
      - The hostname or IP address of the VergeOS system.
    type: str
    required: true
    env:
      - name: VERGEOS_HOST
  username:
    description:
      - The username for authenticating with VergeOS.
    type: str
    required: true
    env:
      - name: VERGEOS_USERNAME
  password:
    description:
      - The password for authenticating with VergeOS.
    type: str
    required: true
    env:
      - name: VERGEOS_PASSWORD
  insecure:
    description:
      - If set to C(true), SSL certificates will not be validated.
      - This should only be used on personally controlled sites using self-signed certificates.
    type: bool
    default: false
    env:
      - name: VERGEOS_INSECURE
  tenants:
    description:
      - List of tenant names to include in inventory.
      - If not specified, all tenants will be included.
    type: list
    elements: str
    required: false
  clusters:
    description:
      - List of cluster names to include in inventory.
      - If not specified, all clusters will be included.
    type: list
    elements: str
    required: false
  filters:
    description:
      - Additional filters to apply when querying VMs.
      - Dictionary of field names and values to match.
    type: dict
    required: false
  hostnames:
    description:
      - A list of templates in order of precedence to compose inventory_hostname.
      - Ignores template if it evaluates to an empty string or None.
      - Template expressions can use variables like C(name), C(machine), etc.
    type: list
    elements: str
    default: ['name']
  include_snapshots:
    description:
      - Include VM snapshots in the inventory.
    type: bool
    default: false
  include_networks:
    description:
      - Include network information in host variables.
    type: bool
    default: true
  include_drives:
    description:
      - Include drive information in host variables (requires per-VM API calls).
    type: bool
    default: false
  strict:
    description:
      - If C(true), the plugin will fail on template errors in hostname, compose, groups, or keyed_groups.
      - If C(false), will skip hosts with template errors.
    type: bool
    default: false
'''

EXAMPLES = r'''
# Minimal configuration
# inventory/vergeos.yml
plugin: vergeio.vergeos.vergeos
host: "192.168.1.100"
username: "admin"
password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
insecure: true

# Advanced configuration with grouping
# inventory/vergeos.yml
plugin: vergeio.vergeos.vergeos
host: "{{ lookup('env', 'VERGEOS_HOST') }}"
username: "{{ lookup('env', 'VERGEOS_USERNAME') }}"
password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"
insecure: true

# Filtering
tenants:
  - "production"
  - "staging"

# Custom hostname template
hostnames:
  - 'name'
  - 'machine'

# Include additional data
include_snapshots: true
include_networks: true
include_drives: true

# Compose variables
compose:
  ansible_host: vergeos_ip | default(None)
  vcpus: cpu_cores
  memory_mb: ram

# Create groups
groups:
  powered_on: vergeos_power_state == "running"
  powered_off: vergeos_power_state == "stopped"
  linux: os_family == "linux"
  windows: os_family == "windows"

# Create keyed groups
keyed_groups:
  - key: cluster
    prefix: cluster
    separator: "_"
  - key: os_family
    prefix: os
    separator: "_"
  - key: vergeos_enabled | ternary('enabled', 'disabled')
    prefix: state
    separator: "_"

# Use with constructed plugin for additional flexibility
# inventory/constructed.yml
plugin: constructed
strict: false
groups:
  production: "'prod' in (vergeos_tags | default([]))"
  development: "'dev' in (vergeos_tags | default([]))"
'''

from ansible.errors import AnsibleError
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable

# SDK Integration
try:
    from pyvergeos import VergeClient
    from pyvergeos.exceptions import (
        NotFoundError,
        AuthenticationError,
        ValidationError,
        APIError,
        VergeConnectionError,
    )
    HAS_PYVERGEOS = True
except ImportError:
    HAS_PYVERGEOS = False


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    """VergeOS dynamic inventory plugin"""

    NAME = 'vergeos'

    def verify_file(self, path):
        """Verify that the source file can be processed correctly"""
        if super(InventoryModule, self).verify_file(path):
            # Accept both .yml and .yaml
            if path.endswith(('vergeos.yml', 'vergeos.yaml', '.vergeos.yml', '.vergeos.yaml')):
                return True
        return False

    def _init_client(self):
        """Initialize the pyvergeos SDK client"""
        if not HAS_PYVERGEOS:
            raise AnsibleError(
                "The pyvergeos SDK is required for this inventory plugin. "
                "Install it with: pip install pyvergeos"
            )

        # Strip protocol prefix if present (SDK expects hostname only)
        host = self.host
        if host.startswith('https://'):
            host = host[8:]
        elif host.startswith('http://'):
            host = host[7:]

        try:
            self.client = VergeClient(
                host=host,
                username=self.username,
                password=self.password,
                verify_ssl=self.validate_certs
            )
        except AuthenticationError as e:
            raise AnsibleError(f"Authentication failed: {e}")
        except VergeConnectionError as e:
            raise AnsibleError(f"Connection to VergeOS failed: {e}")
        except Exception as e:
            raise AnsibleError(f"Failed to initialize VergeOS client: {e}")

    def _get_vms(self):
        """Get all VMs from VergeOS using SDK"""
        try:
            return [dict(vm) for vm in self.client.vms.list()]
        except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
            raise AnsibleError(f"Failed to retrieve VMs: {e}")
        except Exception as e:
            raise AnsibleError(f"Failed to retrieve VMs: {str(e)}")

    def _get_vm_details(self, vm_id):
        """Get detailed information for a specific VM using SDK"""
        try:
            vm = self.client.vms.get(id=vm_id)
            return dict(vm)
        except NotFoundError:
            self.display.vvv(f"VM {vm_id} not found")
            return {}
        except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
            self.display.vvv(f"Failed to get details for VM {vm_id}: {e}")
            return {}
        except Exception as e:
            self.display.vvv(f"Failed to get details for VM {vm_id}: {str(e)}")
            return {}

    def _get_vm_drives(self, vm_id):
        """Get drives for a specific VM using SDK"""
        try:
            vm = self.client.vms.get(id=vm_id)
            return [dict(drive) for drive in vm.drives.list()]
        except NotFoundError:
            self.display.vvv(f"VM {vm_id} not found when getting drives")
            return []
        except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
            self.display.vvv(f"Failed to get drives for VM {vm_id}: {e}")
            return []
        except Exception as e:
            self.display.vvv(f"Failed to get drives for VM {vm_id}: {str(e)}")
            return []

    def _get_networks(self):
        """Get all networks from VergeOS using SDK"""
        try:
            return [dict(net) for net in self.client.vnets.list()]
        except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
            self.display.vvv(f"Failed to retrieve networks: {e}")
            return []
        except Exception as e:
            self.display.vvv(f"Failed to retrieve networks: {str(e)}")
            return []

    def _get_nics(self):
        """Get all NICs from VergeOS using SDK"""
        try:
            return [dict(nic) for nic in self.client.nics.list()]
        except (AuthenticationError, ValidationError, APIError, VergeConnectionError) as e:
            self.display.vvv(f"Failed to retrieve NICs: {e}")
            return []
        except Exception as e:
            self.display.vvv(f"Failed to retrieve NICs: {str(e)}")
            return []

    def _should_include_vm(self, vm):
        """Check if VM should be included based on filters"""
        # Filter by snapshot status
        if not self.include_snapshots and vm.get('is_snapshot'):
            return False

        # Filter by tenant
        if self.limit_tenants:
            vm_tenant = vm.get('tenant')
            if vm_tenant not in self.limit_tenants:
                return False

        # Filter by cluster
        if self.limit_clusters:
            vm_cluster = vm.get('cluster')
            if vm_cluster not in self.limit_clusters:
                return False

        # Apply custom filters
        if self.filters:
            for field, value in self.filters.items():
                if vm.get(field) != value:
                    return False

        return True

    def _get_hostname(self, vm):
        """Determine the hostname for the inventory"""
        for hostname_template in self.hostname_templates:
            try:
                # Simple variable substitution for common fields
                hostname = hostname_template
                for key, value in vm.items():
                    placeholder = f"{{{key}}}"
                    if placeholder in hostname and value:
                        hostname = hostname.replace(placeholder, str(value))

                # Return first non-empty hostname
                if hostname and hostname != hostname_template:
                    return hostname

            except Exception as e:
                if self.get_option('strict'):
                    raise AnsibleError(f"Error in hostname template: {str(e)}")
                continue

        # Fallback to name or machine ID
        return vm.get('name') or str(vm.get('$key', vm.get('machine', 'unknown')))

    def _set_host_vars(self, host, vm, networks_map, nics_map):
        """Populate host variables"""
        # Core VM properties
        self.inventory.set_variable(host, 'vergeos_vm_id', vm.get('$key'))
        self.inventory.set_variable(host, 'vergeos_name', vm.get('name'))
        self.inventory.set_variable(host, 'vergeos_machine', vm.get('machine'))
        self.inventory.set_variable(host, 'vergeos_enabled', vm.get('enabled', True))
        self.inventory.set_variable(host, 'vergeos_is_snapshot', vm.get('is_snapshot', False))

        # Power state
        # Note: VergeOS doesn't have a direct power_state field, infer from machine status
        self.inventory.set_variable(host, 'vergeos_power_state', 'running' if vm.get('machine') else 'stopped')

        # Resource allocation
        self.inventory.set_variable(host, 'cpu_cores', vm.get('cpu_cores'))
        self.inventory.set_variable(host, 'ram', vm.get('ram'))
        self.inventory.set_variable(host, 'machine_type', vm.get('machine_type'))

        # OS information
        self.inventory.set_variable(host, 'os_family', vm.get('os_family'))
        self.inventory.set_variable(host, 'os_description', vm.get('os_description'))

        # Tenant and cluster
        self.inventory.set_variable(host, 'cluster', vm.get('cluster'))
        self.inventory.set_variable(host, 'tenant', vm.get('tenant'))

        # Description and metadata
        self.inventory.set_variable(host, 'description', vm.get('description'))
        self.inventory.set_variable(host, 'created', vm.get('created'))
        self.inventory.set_variable(host, 'modified', vm.get('modified'))

        # Network information
        if self.include_networks and nics_map:
            vm_id = vm.get('$key')
            vm_nics = [nic for nic in nics_map.values() if nic.get('machine') == vm_id]

            if vm_nics:
                self.inventory.set_variable(host, 'vergeos_nics', vm_nics)

                # Try to find primary IP for ansible_host
                primary_ip = None
                for nic in vm_nics:
                    if nic.get('ip_address'):
                        primary_ip = nic['ip_address']
                        break

                if primary_ip:
                    self.inventory.set_variable(host, 'vergeos_ip', primary_ip)

        # Snapshot parent
        if vm.get('is_snapshot'):
            self.inventory.set_variable(host, 'parent_vm', vm.get('parent_vm'))

        # Tags/labels (if available)
        if 'tags' in vm:
            self.inventory.set_variable(host, 'vergeos_tags', vm.get('tags', []))

        # Raw VM data for advanced use
        self.inventory.set_variable(host, 'vergeos_vm_data', vm)

    def _add_to_groups(self, host, vm):
        """Add host to appropriate groups"""
        # Group by cluster
        cluster = vm.get('cluster')
        if cluster:
            cluster_group = f"cluster_{self._sanitize_group_name(str(cluster))}"
            self.inventory.add_group(cluster_group)
            self.inventory.add_child(cluster_group, host)

        # Group by tenant
        tenant = vm.get('tenant')
        if tenant:
            tenant_group = f"tenant_{self._sanitize_group_name(str(tenant))}"
            self.inventory.add_group(tenant_group)
            self.inventory.add_child(tenant_group, host)

        # Group by OS family
        os_family = vm.get('os_family')
        if os_family:
            os_group = f"os_{self._sanitize_group_name(os_family)}"
            self.inventory.add_group(os_group)
            self.inventory.add_child(os_group, host)

        # Group by power state
        if vm.get('machine'):
            self.inventory.add_group('powered_on')
            self.inventory.add_child('powered_on', host)
        else:
            self.inventory.add_group('powered_off')
            self.inventory.add_child('powered_off', host)

        # Group snapshots
        if vm.get('is_snapshot'):
            self.inventory.add_group('snapshots')
            self.inventory.add_child('snapshots', host)
        else:
            self.inventory.add_group('vms')
            self.inventory.add_child('vms', host)

    def _sanitize_group_name(self, name):
        """Sanitize group name to be Ansible-compliant"""
        import re
        # Replace invalid characters with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', str(name))
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized.lower()

    def parse(self, inventory, loader, path, cache=True):
        """Parse the inventory source"""
        super(InventoryModule, self).parse(inventory, loader, path, cache)

        # Read the inventory source file
        self._read_config_data(path)

        # Get connection parameters
        self.host = self.get_option('host')
        self.username = self.get_option('username')
        self.password = self.get_option('password')
        self.insecure = self.get_option('insecure')
        self.validate_certs = not self.insecure

        # Initialize SDK client
        self._init_client()

        # Get filtering options
        self.limit_tenants = set(self.get_option('tenants') or [])
        self.limit_clusters = set(self.get_option('clusters') or [])
        self.filters = self.get_option('filters') or {}

        # Get options
        self.include_snapshots = self.get_option('include_snapshots')
        self.include_networks = self.get_option('include_networks')
        self.include_drives = self.get_option('include_drives')
        self.hostname_templates = self.get_option('hostnames')

        # Use cache if available
        cache_key = self.get_cache_key(path)
        use_cache = self.get_option('cache') and cache

        # Try to use cache
        if use_cache:
            try:
                cached_data = self._cache[cache_key]
                self._populate_from_cache(cached_data)
                return
            except KeyError:
                pass

        # Fetch data from API
        self.display.vvv("Fetching VMs from VergeOS")
        vms = self._get_vms()
        self.display.vvv(f"Found {len(vms)} VMs")

        # Fetch networks and NICs if requested
        networks = []
        nics = []
        networks_map = {}
        nics_map = {}

        if self.include_networks:
            self.display.vvv("Fetching networks")
            networks = self._get_networks()
            networks_map = {net.get('$key'): net for net in networks}

            self.display.vvv("Fetching NICs")
            nics = self._get_nics()
            nics_map = {nic.get('$key'): nic for nic in nics}

        # Process VMs
        strict = self.get_option('strict')

        for vm in vms:
            # Apply filters
            if not self._should_include_vm(vm):
                continue

            # Determine hostname
            hostname = self._get_hostname(vm)

            # Add host to inventory
            self.inventory.add_host(hostname)

            # Populate host variables
            self._set_host_vars(hostname, vm, networks_map, nics_map)

            # Add to groups
            self._add_to_groups(hostname, vm)

            # Get drives if requested
            if self.include_drives:
                vm_id = vm.get('$key')
                if vm_id:
                    drives = self._get_vm_drives(vm_id)
                    if drives:
                        self.inventory.set_variable(hostname, 'vergeos_drives', drives)

            # Apply constructed features for this host
            self._set_composite_vars(
                self.get_option('compose'),
                self.inventory.get_host(hostname).get_vars(),
                hostname,
                strict
            )
            self._add_host_to_composed_groups(
                self.get_option('groups'),
                {},
                hostname,
                strict
            )
            self._add_host_to_keyed_groups(
                self.get_option('keyed_groups'),
                {},
                hostname,
                strict
            )

        # Update cache
        if use_cache:
            self._cache[cache_key] = self._get_cache_data()

    def _populate_from_cache(self, cache_data):
        """Populate inventory from cached data"""
        # This is a simplified cache implementation
        # In production, you'd want to serialize/deserialize the entire inventory state
        pass

    def _get_cache_data(self):
        """Get data to cache"""
        # Return serializable cache data
        return {
            'hosts': list(self.inventory.hosts.keys()),
            'groups': list(self.inventory.groups.keys())
        }
