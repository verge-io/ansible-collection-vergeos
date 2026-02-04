# -*- coding: utf-8 -*-

# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
name: vergeos_vms
short_description: Multi-site VergeOS VM dynamic inventory
version_added: "2.0.0"
author:
  - VergeIO (@vergeio)
description:
  - Queries multiple VergeOS sites concurrently for VMs.
  - Groups by site, tags, tenant, status, os_family, and cluster.
  - Supports caching for large deployments (100+ sites).
  - Uses pyvergeos SDK for API operations.
  - API-only plugin - does NOT set ansible_host or support SSH to VMs.
  - All operations are performed via the VergeOS API, not direct VM connections.
requirements:
  - python >= 3.9
  - pyvergeos >= 1.0.0
extends_documentation_fragment:
  - constructed
  - inventory_cache
options:
  plugin:
    description: Token identifying this as vergeos_vms plugin.
    required: true
    choices: ['vergeos_vms', 'vergeio.vergeos.vergeos_vms']
  sites:
    description:
      - List of VergeOS sites to query.
      - Each site requires name, host, and credentials (username/password or api_key).
      - For single-site usage, provide a list with one site.
    type: list
    elements: dict
    required: true
    suboptions:
      name:
        description: Unique identifier for this site (used in group names and host prefixes).
        type: str
        required: true
      host:
        description: Hostname or IP of the VergeOS system.
        type: str
        required: true
      username:
        description: Username for authentication (required if api_key not provided).
        type: str
      password:
        description: Password for authentication (required if api_key not provided).
        type: str
      api_key:
        description: API key/token for authentication (alternative to username/password).
        type: str
      insecure:
        description: Skip SSL certificate verification.
        type: bool
        default: false
      timeout:
        description: Connection timeout in seconds.
        type: int
        default: 30
  filters:
    description: Filters to apply to VM queries.
    type: dict
    suboptions:
      status:
        description: Filter VMs by status (running, stopped, etc.).
        type: str
      name_pattern:
        description: Regex pattern to filter VM names.
        type: str
  group_by:
    description: Dimensions to group hosts by.
    type: list
    elements: str
    default: ['site', 'status']
  max_workers:
    description: Maximum concurrent site queries.
    type: int
    default: 10
  site_timeout:
    description: Timeout for each site query in seconds.
    type: int
    default: 60
  hostname_template:
    description: Template for inventory hostname. Use {site} and {name} placeholders.
    type: str
    default: '{site}_{name}'
  hostvar_prefix:
    description: Prefix for VergeOS-specific host variables.
    type: str
    default: 'vergeos_'
  include_stopped:
    description: Include stopped/powered-off VMs in inventory.
    type: bool
    default: true
  strict:
    description:
      - If C(true), the plugin will fail on template errors.
      - If C(false), will skip hosts with template errors.
    type: bool
    default: false
'''

EXAMPLES = r'''
# Single-site configuration (replacement for vergeos.py)
plugin: vergeio.vergeos.vergeos_vms
sites:
  - name: production
    host: vergeos.example.com
    username: admin
    password: "{{ lookup('env', 'VERGEOS_PASSWORD') }}"

# Multi-site configuration
plugin: vergeio.vergeos.vergeos_vms
sites:
  - name: denver
    host: denver.vergeos.local
    username: admin
    password: "{{ lookup('env', 'DENVER_PASS') }}"
  - name: chicago
    host: chicago.vergeos.local
    username: admin
    password: "{{ lookup('env', 'CHICAGO_PASS') }}"

# Full configuration with caching and filtering
plugin: vergeio.vergeos.vergeos_vms
cache: true
cache_plugin: jsonfile
cache_connection: ~/.cache/vergeos_inventory
cache_timeout: 900

sites:
  - name: denver
    host: denver.vergeos.local
    api_key: "{{ lookup('env', 'DENVER_API_KEY') }}"
    insecure: true
  - name: chicago
    host: chicago.vergeos.local
    username: admin
    password: "{{ lookup('env', 'CHICAGO_PASS') }}"
    timeout: 60

filters:
  status: running
  name_pattern: ".*web.*"

group_by:
  - site
  - tags
  - status
  - tenant

max_workers: 20
site_timeout: 120

# Create custom groups using constructed features
groups:
  production: "'prod' in (vergeos_tags | default([]))"
  webservers: "'web' in vergeos_name"

keyed_groups:
  - key: vergeos_site
    prefix: site
  - key: vergeos_status
    prefix: status
'''

import re
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from ansible.errors import AnsibleError
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable

# SDK Integration
try:
    from pyvergeos import VergeClient
    from pyvergeos.exceptions import (
        AuthenticationError,
        VergeConnectionError,
    )
    HAS_PYVERGEOS = True
except ImportError:
    HAS_PYVERGEOS = False


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    """Multi-site VergeOS VM dynamic inventory plugin.

    This plugin queries multiple VergeOS sites concurrently and builds
    an Ansible inventory from VMs across all sites.

    IMPORTANT: This is an API-only plugin. It does NOT set ansible_host
    and does not support SSH connections to VMs. All operations are
    performed via the VergeOS API using the site URL and VM identifiers
    provided in host variables.
    """

    NAME = 'vergeos_vms'

    def verify_file(self, path):
        """Verify that the source file can be processed correctly."""
        if super(InventoryModule, self).verify_file(path):
            # Accept .vergeos_vms.yml/.yaml extensions
            if path.endswith(('.vergeos_vms.yml', '.vergeos_vms.yaml')):
                return True
        return False

    def _fetch_site(self, site_config):
        """Fetch VMs from a single site via VergeOS API.

        Uses batch API calls to fetch all VMs, tags, and NICs efficiently.
        This makes only 4 API calls regardless of VM count:
        1. vms.list() - all VMs
        2. tags.list() - tag definitions (for name mapping)
        3. tag_members - all tag-to-VM assignments
        4. machine_nics - all NICs

        Args:
            site_config: Dictionary with site connection details.

        Returns:
            Dictionary with site data including VMs, NICs, and any errors.
        """
        site_name = site_config['name']
        host = site_config['host']

        # Strip protocol if present (SDK expects hostname only)
        if host.startswith('https://'):
            host = host[8:]
        elif host.startswith('http://'):
            host = host[7:]

        # Build connection kwargs
        conn_kwargs = {
            'host': host,
            'verify_ssl': not site_config.get('insecure', False),
            'timeout': site_config.get('timeout', 30),
        }

        # SDK uses 'token' parameter, inventory config uses 'api_key'
        if site_config.get('api_key'):
            conn_kwargs['token'] = site_config['api_key']
        else:
            conn_kwargs['username'] = site_config.get('username')
            conn_kwargs['password'] = site_config.get('password')

        try:
            client = VergeClient(**conn_kwargs)

            # === BATCH FETCH: 4 API calls total ===

            # 1. Get all VMs
            vms = list(client.vms.list())

            # Build machine ID -> VM index mapping for joins
            vm_by_machine = {}
            vm_data = []
            for vm in vms:
                vm_dict = dict(vm)
                vm_dict['_tags'] = []
                vm_dict['_nics'] = []
                machine_id = vm_dict.get('machine')
                if machine_id:
                    vm_by_machine[machine_id] = vm_dict
                vm_data.append(vm_dict)

            # 2. Get tag definitions (for ID -> name mapping)
            tag_name_map = {}
            try:
                tags = list(client.tags.list())
                tag_name_map = {dict(t)['$key']: dict(t)['name'] for t in tags}
            except Exception:
                pass  # Tags not available, continue without them

            # 3. Get all tag memberships in one call
            try:
                tag_members = client._request('GET', 'tag_members', params={'fields': 'all'})
                for tm in tag_members:
                    tag_id = tm.get('tag')
                    member = tm.get('member', '')  # format: 'vms/34'
                    if member.startswith('vms/'):
                        try:
                            vm_key = int(member.split('/')[1])
                            # Find the VM by $key and add the tag
                            for vm_dict in vm_data:
                                if vm_dict.get('$key') == vm_key:
                                    tag_name = tag_name_map.get(tag_id)
                                    if tag_name:
                                        vm_dict['_tags'].append(tag_name)
                                    break
                        except (ValueError, IndexError):
                            pass
            except Exception:
                pass  # Tag members not available, continue without them

            # 4. Get all NICs in one call
            try:
                all_nics = client._request('GET', 'machine_nics', params={'fields': 'all'})
                for nic in all_nics:
                    machine_id = nic.get('machine')
                    if machine_id in vm_by_machine:
                        vm_by_machine[machine_id]['_nics'].append(nic)
            except Exception:
                pass  # NICs not available, continue without them

            return {
                'site': site_name,
                'site_url': site_config['host'],
                'vms': vm_data,
                'error': None
            }

        except AuthenticationError as e:
            return {
                'site': site_name,
                'site_url': site_config['host'],
                'vms': [],
                'error': f"Authentication failed: {e}"
            }
        except VergeConnectionError as e:
            return {
                'site': site_name,
                'site_url': site_config['host'],
                'vms': [],
                'error': f"Connection failed: {e}"
            }
        except Exception as e:
            return {
                'site': site_name,
                'site_url': site_config['host'],
                'vms': [],
                'error': str(e)
            }

    def _fetch_all_sites(self):
        """Fetch VMs from all configured sites concurrently.

        Returns:
            List of site data dictionaries.
        """
        sites = self.get_option('sites')
        max_workers = self.get_option('max_workers')
        site_timeout = self.get_option('site_timeout')

        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_site = {
                executor.submit(self._fetch_site, site): site
                for site in sites
            }

            for future in as_completed(future_to_site):
                site = future_to_site[future]
                try:
                    result = future.result(timeout=site_timeout)
                    if result['error']:
                        self.display.warning(
                            f"Site '{site['name']}' returned error: {result['error']}"
                        )
                    else:
                        self.display.vvv(
                            f"Site '{site['name']}': fetched {len(result['vms'])} VMs"
                        )
                    results.append(result)
                except FuturesTimeoutError:
                    self.display.warning(
                        f"Site '{site['name']}' timed out after {site_timeout}s"
                    )
                    results.append({
                        'site': site['name'],
                        'site_url': site.get('host', ''),
                        'vms': [],
                        'nics': [],
                        'error': f"Timeout after {site_timeout}s"
                    })
                except Exception as e:
                    self.display.warning(
                        f"Site '{site['name']}' failed: {e}"
                    )
                    results.append({
                        'site': site['name'],
                        'site_url': site.get('host', ''),
                        'vms': [],
                        'nics': [],
                        'error': str(e)
                    })

        return results

    def _matches_filters(self, vm):
        """Check if VM matches configured filters.

        Args:
            vm: Dictionary of VM data.

        Returns:
            True if VM matches all filters, False otherwise.
        """
        filters = self.get_option('filters') or {}

        if not filters:
            return True

        # Status filter
        if 'status' in filters:
            vm_status = vm.get('status', vm.get('power_state'))
            if vm_status != filters['status']:
                return False

        # Name pattern filter
        if 'name_pattern' in filters:
            pattern = filters['name_pattern']
            vm_name = vm.get('name', '')
            if not re.search(pattern, vm_name):
                return False

        # Generic field filters
        for field, value in filters.items():
            if field in ('status', 'name_pattern'):
                continue
            if vm.get(field) != value:
                return False

        return True

    def _sanitize_group_name(self, name):
        """Sanitize group name to be Ansible-compliant.

        Args:
            name: Raw name string.

        Returns:
            Sanitized group name.
        """
        # Replace invalid characters with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', str(name))
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized.lower()

    def _get_hostname(self, vm, site_name):
        """Generate inventory hostname from template.

        Args:
            vm: Dictionary of VM data.
            site_name: Name of the site.

        Returns:
            Inventory hostname string.
        """
        template = self.get_option('hostname_template')
        hostname = template.replace('{site}', site_name)
        hostname = hostname.replace('{name}', vm.get('name', str(vm.get('$key', 'unknown'))))

        # Sanitize hostname
        hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', hostname)
        return hostname

    def _create_groups(self, hostname, vm, site_name):
        """Add host to groups based on group_by configuration.

        Args:
            hostname: Inventory hostname.
            vm: Dictionary of VM data.
            site_name: Name of the site.
        """
        group_by = self.get_option('group_by') or ['site', 'status']

        if 'site' in group_by:
            group = f"site_{self._sanitize_group_name(site_name)}"
            self.inventory.add_group(group)
            self.inventory.add_child(group, hostname)

        if 'status' in group_by:
            status = vm.get('status', 'unknown')
            group = f"status_{self._sanitize_group_name(status)}"
            self.inventory.add_group(group)
            self.inventory.add_child(group, hostname)

        if 'tags' in group_by:
            vm_tags = vm.get('_tags', [])
            for tag in vm_tags:
                group = f"tag_{self._sanitize_group_name(tag)}"
                self.inventory.add_group(group)
                self.inventory.add_child(group, hostname)

        if 'tenant' in group_by:
            tenant = vm.get('tenant')
            if tenant:
                group = f"tenant_{self._sanitize_group_name(tenant)}"
                self.inventory.add_group(group)
                self.inventory.add_child(group, hostname)

        if 'os_family' in group_by:
            os_family = vm.get('os_family')
            if os_family:
                group = f"os_{self._sanitize_group_name(os_family)}"
                self.inventory.add_group(group)
                self.inventory.add_child(group, hostname)

        if 'cluster' in group_by:
            cluster = vm.get('cluster')
            if cluster:
                group = f"cluster_{self._sanitize_group_name(cluster)}"
                self.inventory.add_group(group)
                self.inventory.add_child(group, hostname)

    def _set_hostvars(self, hostname, vm, site_name, site_url):
        """Set all host variables for a VM.

        IMPORTANT: This method does NOT set ansible_host. This is an
        API-only plugin - all VM operations are performed via the
        VergeOS API, not direct SSH connections.

        Args:
            hostname: Inventory hostname.
            vm: Dictionary of VM data (includes _tags and _nics from fetch).
            site_name: Name of the site.
            site_url: URL of the site API.
        """
        prefix = self.get_option('hostvar_prefix')

        # Site info (used by modules to connect to correct VergeOS API)
        self.inventory.set_variable(hostname, f'{prefix}site', site_name)
        self.inventory.set_variable(hostname, f'{prefix}site_url', site_url)

        # VM identification
        self.inventory.set_variable(hostname, f'{prefix}vm_id', vm.get('$key'))
        self.inventory.set_variable(hostname, f'{prefix}name', vm.get('name'))
        self.inventory.set_variable(hostname, f'{prefix}machine', vm.get('machine'))

        # Status
        self.inventory.set_variable(hostname, f'{prefix}status', vm.get('status'))
        self.inventory.set_variable(hostname, f'{prefix}enabled', vm.get('enabled', True))

        # Resources
        self.inventory.set_variable(hostname, f'{prefix}ram', vm.get('ram'))
        self.inventory.set_variable(hostname, f'{prefix}cpu_cores', vm.get('cpu_cores'))

        # OS info
        self.inventory.set_variable(hostname, f'{prefix}os_family', vm.get('os_family'))
        self.inventory.set_variable(hostname, f'{prefix}os_description', vm.get('os_description'))

        # Organization
        self.inventory.set_variable(hostname, f'{prefix}tenant', vm.get('tenant'))
        self.inventory.set_variable(hostname, f'{prefix}cluster', vm.get('cluster'))

        # Tags (fetched during _fetch_site via vm.get_tags())
        self.inventory.set_variable(hostname, f'{prefix}tags', vm.get('_tags', []))

        # Network info (for reference, NOT for SSH)
        # NICs are fetched per-VM during _fetch_site via vm.nics.list()
        vm_nics = vm.get('_nics', [])
        if vm_nics:
            self.inventory.set_variable(hostname, f'{prefix}nics', vm_nics)
            # Store IP for reference (user can compose ansible_host if they really need SSH)
            for nic in vm_nics:
                ip = nic.get('ipaddress') or nic.get('ip_address')
                if ip:
                    self.inventory.set_variable(hostname, f'{prefix}ip', ip)
                    break

        # Raw VM data for advanced use (excluding internal _tags/_nics fields)
        vm_copy = {k: v for k, v in vm.items() if not k.startswith('_')}
        self.inventory.set_variable(hostname, f'{prefix}vm_data', vm_copy)

    def _populate_inventory(self, site_data_list):
        """Populate inventory from fetched site data.

        Args:
            site_data_list: List of site data dictionaries from _fetch_all_sites().
        """
        include_stopped = self.get_option('include_stopped')
        strict = self.get_option('strict')

        for site_data in site_data_list:
            if site_data['error']:
                # Skip sites with errors (already warned in _fetch_all_sites)
                continue

            site_name = site_data['site']
            site_url = site_data['site_url']

            for vm in site_data['vms']:
                # Skip snapshots
                if vm.get('is_snapshot'):
                    continue

                # Skip stopped VMs if not included
                if not include_stopped:
                    status = vm.get('status', '')
                    if status in ('stopped', 'offline', 'powered_off'):
                        continue

                # Apply filters
                if not self._matches_filters(vm):
                    continue

                # Generate hostname
                hostname = self._get_hostname(vm, site_name)

                # Add host to inventory
                self.inventory.add_host(hostname)

                # Set host variables (NO ansible_host - API only)
                # NICs are embedded in vm dict as _nics from _fetch_site
                self._set_hostvars(hostname, vm, site_name, site_url)

                # Add to groups
                self._create_groups(hostname, vm, site_name)

                # Apply constructed features
                try:
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
                except Exception as e:
                    if strict:
                        raise AnsibleError(f"Error processing host {hostname}: {e}")
                    self.display.warning(f"Error processing host {hostname}: {e}")

    def _get_cache_data(self):
        """Serialize inventory data for caching.

        Returns:
            Dictionary of cache data.
        """
        return {
            'hosts': {
                hostname: dict(self.inventory.get_host(hostname).vars)
                for hostname in self.inventory.hosts
            },
            'groups': {
                groupname: [h.name for h in self.inventory.groups[groupname].hosts]
                for groupname in self.inventory.groups
                if groupname != 'all' and groupname != 'ungrouped'
            }
        }

    def _populate_from_cache(self, cached_data):
        """Restore inventory from cached data.

        Args:
            cached_data: Dictionary from _get_cache_data().
        """
        # Restore hosts
        for hostname, hostvars in cached_data.get('hosts', {}).items():
            self.inventory.add_host(hostname)
            for var, value in hostvars.items():
                self.inventory.set_variable(hostname, var, value)

        # Restore groups
        for groupname, hostnames in cached_data.get('groups', {}).items():
            self.inventory.add_group(groupname)
            for hostname in hostnames:
                if hostname in self.inventory.hosts:
                    self.inventory.add_child(groupname, hostname)

    def parse(self, inventory, loader, path, cache=True):
        """Parse the inventory source.

        Args:
            inventory: Ansible inventory object.
            loader: Ansible data loader.
            path: Path to inventory file.
            cache: Whether to use caching.
        """
        super(InventoryModule, self).parse(inventory, loader, path, cache)

        # Check for SDK
        if not HAS_PYVERGEOS:
            raise AnsibleError(
                "The pyvergeos SDK is required for this inventory plugin. "
                "Install it with: pip install pyvergeos"
            )

        # Read config
        self._read_config_data(path)

        # Validate sites configuration
        sites = self.get_option('sites')
        if not sites:
            raise AnsibleError("At least one site must be configured in 'sites'")

        for i, site in enumerate(sites):
            if not site.get('name'):
                raise AnsibleError(f"Site at index {i} is missing required 'name' field")
            if not site.get('host'):
                raise AnsibleError(f"Site '{site.get('name', i)}' is missing required 'host' field")
            if not site.get('api_key') and not (site.get('username') and site.get('password')):
                raise AnsibleError(
                    f"Site '{site['name']}' must have either 'api_key' or "
                    "'username' and 'password' for authentication"
                )

        # Cache handling
        cache_key = self.get_cache_key(path)
        use_cache = self.get_option('cache') and cache

        if use_cache:
            try:
                cached_data = self._cache[cache_key]
                self.display.vvv(f"Loading inventory from cache: {cache_key}")
                self._populate_from_cache(cached_data)
                return
            except KeyError:
                self.display.vvv(f"Cache miss: {cache_key}")

        # Fetch from all sites
        self.display.vvv(f"Fetching VMs from {len(sites)} site(s)")
        site_data = self._fetch_all_sites()

        # Populate inventory
        self._populate_inventory(site_data)

        # Update cache
        if use_cache:
            self._cache[cache_key] = self._get_cache_data()
