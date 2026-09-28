"""The OVF the image_pipeline role writes, rendered and read back.

The role's central promise is that a golden template carries **no network
binding**, because one baked into the template drags itself into every VM
ever cloned from it. Until now the only thing checking that was the live
ladder, which needs a cluster, an upload and an import to find out.

The template is a Jinja file and the OVF is XML. Both can be checked here in
milliseconds, which is the difference between finding a broken envelope
before the upload and finding it after.
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import xml.etree.ElementTree as ET

import pytest
from jinja2 import Environment, FileSystemLoader

GB = 1073741824

NS = {
    'ovf': 'http://schemas.dmtf.org/ovf/envelope/1',
    'rasd': ('http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/'
             'CIM_ResourceAllocationSettingData'),
    'vssd': ('http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/'
             'CIM_VirtualSystemSettingData'),
}

# ResourceType values from the DMTF CIM schema the OVF references.
CPU, MEMORY, ETHERNET, DISK = '3', '4', '10', '17'


def _templates_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(
        here, '..', '..', '..', 'roles', 'image_pipeline', 'templates'))


def render(**over):
    context = {
        'template_name': 'web-golden-v3',
        'template_cores': 2,
        'template_ram_mb': 2048,
        'template_os_family': 'linux',
        '_image_bytes': 4 * GB,
        '_image_virtual_bytes': 40 * GB,
    }
    context.update(over)
    env = Environment(loader=FileSystemLoader(_templates_dir()),
                      keep_trailing_newline=True)
    return env.get_template('golden.ovf.j2').render(**context)


@pytest.fixture(name='ovf')
def ovf_fixture():
    return ET.fromstring(render())


class TestItIsAnEnvelopeAtAll:
    def test_it_parses_as_xml(self):
        """An unparseable OVF is discovered after the upload, otherwise."""
        ET.fromstring(render())

    def test_the_root_is_an_ovf_envelope(self, ovf):
        assert ovf.tag == '{%s}Envelope' % NS['ovf']

    def test_a_name_with_an_ampersand_does_not_break_it(self):
        """Jinja does not escape XML by default, and VM names are free text.

        `R&D-golden` is an ordinary name and, unescaped, an invalid XML
        document -- the OVA packs, uploads, and fails at import with a parse
        error about a file the operator never wrote. Same family as issue
        #32, which was apostrophes and braces in catalog names.
        """
        ovf = ET.fromstring(render(template_name='R&D-golden'))
        assert ovf.findtext('ovf:VirtualSystem/ovf:Name',
                            namespaces=NS) == 'R&D-golden'

    def test_the_escaped_href_still_names_the_file_that_is_packed(self):
        """Escaping is markup, not content. A consumer unescapes ovf:href
        back to the name `cp` and `tar` actually used."""
        ovf = ET.fromstring(render(template_name='R&D-golden'))
        ref = ovf.find('ovf:References/ovf:File', NS)
        assert ref.get('{%s}href' % NS['ovf']) == 'R&D-golden.qcow2'

    def test_angle_brackets_and_quotes_survive_too(self):
        ovf = ET.fromstring(render(template_name='a<b>"c"'))
        assert ovf.findtext('ovf:VirtualSystem/ovf:Name',
                            namespaces=NS) == 'a<b>"c"'


class TestNoNetworkBinding:
    """The promise the whole role is built around."""

    def test_there_is_no_network_section(self, ovf):
        assert ovf.find('ovf:NetworkSection', NS) is None

    def test_no_hardware_item_is_an_ethernet_adapter(self, ovf):
        types = [item.findtext('rasd:ResourceType', namespaces=NS)
                 for item in ovf.iter('{%s}Item' % NS['ovf'])]
        assert ETHERNET not in types, (
            'the OVF declares an ethernet adapter, so every VM cloned from '
            'this template inherits a network binding it never asked for')

    def test_and_nothing_mentions_a_network_at_all(self):
        rendered = render().lower()
        assert 'networksection' not in rendered
        assert 'ovf:network' not in rendered


class TestTheDiskIsDescribedHonestly:
    def test_capacity_is_the_virtual_size_not_the_file_size(self, ovf):
        """A qcow2 is sparse. `ovf:capacity` is the size the guest sees.

        Using the file size would import a 40 GB image as a 4 GB disk and
        the guest filesystem would not fit in it.
        """
        disk = ovf.find('ovf:DiskSection/ovf:Disk', NS)
        assert disk.get('{%s}capacity' % NS['ovf']) == str(40 * GB)
        assert disk.get('{%s}capacityAllocationUnits' % NS['ovf']) == 'byte'

    def test_the_file_reference_carries_the_file_size(self, ovf):
        ref = ovf.find('ovf:References/ovf:File', NS)
        assert ref.get('{%s}size' % NS['ovf']) == str(4 * GB)

    def test_the_disk_points_at_the_file_that_is_packed(self, ovf):
        ref = ovf.find('ovf:References/ovf:File', NS)
        disk = ovf.find('ovf:DiskSection/ovf:Disk', NS)
        assert ref.get('{%s}id' % NS['ovf']) == \
            disk.get('{%s}fileRef' % NS['ovf'])
        assert ref.get('{%s}href' % NS['ovf']) == 'web-golden-v3.qcow2'

    def test_the_format_is_qcow2(self, ovf):
        disk = ovf.find('ovf:DiskSection/ovf:Disk', NS)
        assert 'qcow2' in disk.get('{%s}format' % NS['ovf'])

    def test_there_is_exactly_one_disk(self, ovf):
        types = [item.findtext('rasd:ResourceType', namespaces=NS)
                 for item in ovf.iter('{%s}Item' % NS['ovf'])]
        assert types.count(DISK) == 1


class TestTheSpecsArriveInTheUnitsTheyClaim:
    """The role declares RAM in MB. The OVF has to say so.

    `rasd:AllocationUnits` is the only thing that distinguishes 2048 MB from
    2048 bytes, and a consumer that reads the number without the unit builds
    a VM with 2 KB of memory.
    """

    def test_memory_is_declared_in_mebibytes(self, ovf):
        item = [i for i in ovf.iter('{%s}Item' % NS['ovf'])
                if i.findtext('rasd:ResourceType', namespaces=NS) == MEMORY][0]
        assert item.findtext('rasd:AllocationUnits',
                             namespaces=NS) == 'byte * 2^20'
        assert item.findtext('rasd:VirtualQuantity',
                             namespaces=NS) == '2048'

    def test_cpu_count_is_the_one_asked_for(self, ovf):
        item = [i for i in ovf.iter('{%s}Item' % NS['ovf'])
                if i.findtext('rasd:ResourceType', namespaces=NS) == CPU][0]
        assert item.findtext('rasd:VirtualQuantity', namespaces=NS) == '2'

    def test_the_specs_follow_the_variables(self):
        ovf = ET.fromstring(render(template_cores=8, template_ram_mb=16384))
        quantities = {
            i.findtext('rasd:ResourceType', namespaces=NS):
                i.findtext('rasd:VirtualQuantity', namespaces=NS)
            for i in ovf.iter('{%s}Item' % NS['ovf'])}
        assert quantities[CPU] == '8'
        assert quantities[MEMORY] == '16384'


class TestIdentity:
    def test_the_virtual_system_is_named_for_the_template(self, ovf):
        system = ovf.find('ovf:VirtualSystem', NS)
        assert system.get('{%s}id' % NS['ovf']) == 'web-golden-v3'
        assert system.findtext('ovf:Name', namespaces=NS) == 'web-golden-v3'

    def test_the_os_family_is_carried_through(self):
        ovf = ET.fromstring(render(template_os_family='windows'))
        assert ovf.findtext(
            'ovf:VirtualSystem/ovf:OperatingSystemSection/ovf:Description',
            namespaces=NS) == 'windows'

    def test_every_instance_id_is_unique(self, ovf):
        """Duplicate InstanceIDs make an importer drop hardware silently."""
        ids = [i.findtext('rasd:InstanceID', namespaces=NS)
               for i in ovf.iter('{%s}Item' % NS['ovf'])]
        assert len(ids) == len(set(ids)), ids

    def test_the_disk_hangs_off_the_controller_that_exists(self, ovf):
        items = list(ovf.iter('{%s}Item' % NS['ovf']))
        by_id = {i.findtext('rasd:InstanceID', namespaces=NS): i
                 for i in items}
        disk = [i for i in items
                if i.findtext('rasd:ResourceType', namespaces=NS) == DISK][0]
        parent = disk.findtext('rasd:Parent', namespaces=NS)
        assert parent in by_id, (
            'the disk names parent %r, which is not an Item in this envelope'
            % parent)
