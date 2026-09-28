"""Shape of the unattended-install seeds and playbooks.

The installer sources user-data as bash only when the first line is not
``#cloud-config``. A joining node in a nested lab needs a static core
address. Those are easy to regress and invisible to syntax-check.
"""

from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "unattended_install"
TEMPLATES = EXAMPLE / "templates"

SEED_CONTEXT = {
    "vlab_name": "vlab",
    "vlab_domain": "lab.local",
    "vlab_timezone": "America/Detroit",
    "vlab_admin_username": "admin",
    "vlab_admin_password": "lab-password",
    "vlab_admin_email": "admin@lab.local",
    "vlab_drive_list": "/dev/sda /dev/sdb /dev/sdc /dev/sdd",
    "vlab_tier_list": "0 0 1 1",
    "vlab_seed_debug": False,
    "vlab_trace_url": "http://trace.example/install",
}


def _as_bool(value):
    """Enough of Ansible's bool filter for the seed's debug flag."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0", ""):
        return False
    raise ValueError(value)


def _env():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["bool"] = _as_bool
    return env


def _render(name, **overrides):
    context = dict(SEED_CONTEXT)
    context.update(overrides)
    return _env().get_template(name).render(**context)


def _first_line(text):
    for line in text.splitlines():
        if line.strip():
            return line
    return ""


def test_seed_files_exist():
    for name in (
        "user-data-node1.sh.j2",
        "user-data-node2.sh.j2",
        "user-data-node3.sh.j2",
    ):
        assert (TEMPLATES / name).is_file()
    for name in (
        "deploy_nested_lab.yml",
        "find_new_system.yml",
        "post_install.yml",
        "README.md",
    ):
        assert (EXAMPLE / name).is_file()
    assert (ROOT / "docs" / "UNATTENDED-INSTALL.md").is_file()


def test_rendered_seeds_are_sourced_as_bash():
    for name in (
        "user-data-node1.sh.j2",
        "user-data-node2.sh.j2",
        "user-data-node3.sh.j2",
    ):
        rendered = _render(name)
        first = _first_line(rendered)
        assert not first.startswith("#cloud-config"), name
        assert "YC_USER_PASSWORD=lab-password" in rendered
        assert "#cloud-config" not in rendered.splitlines()[0]


def test_node_roles_and_static_core_addresses():
    node1 = _render("user-data-node1.sh.j2")
    node2 = _render("user-data-node2.sh.j2")
    node3 = _render("user-data-node3.sh.j2")

    assert "YC_INSTALL_TYPE=controller" in node1
    assert "YC_VSAN_NEW=1" in node1
    assert "YC_NET_EXTERNAL1_ADDR=dhcp" in node1
    assert "YC_NET_CORE_NODE_ADDR='100.96.0.2/24'" in node1

    assert "YC_INSTALL_TYPE=controller" in node2
    assert "YC_VSAN_NEW=0" in node2
    assert "YC_NET_CORE_NODE_ADDR='100.96.0.3/24'" in node2
    assert "YC_CLUSTER=1" in node2

    assert "YC_INSTALL_TYPE=scale-out" in node3
    assert "YC_VSAN_NEW=0" in node3
    assert "YC_NET_CORE_NODE_ADDR='100.96.0.4/24'" in node3
    assert "YC_NET_CORE_NODE_ADDR=dhcp" not in node3
    assert "YC_NET_CORE_NODE_ADDR='dhcp'" not in node3
    assert "YC_CLUSTER=1" in node3


def test_node1_trace_is_off_unless_asked():
    quiet = _render("user-data-node1.sh.j2")
    assert "udhcpc" not in quiet
    assert "trace.example" not in quiet

    # Extra vars arrive as strings. "false" must not turn the trace on.
    also_quiet = _render("user-data-node1.sh.j2", vlab_seed_debug="false")
    assert "udhcpc" not in also_quiet

    traced = _render("user-data-node1.sh.j2", vlab_seed_debug=True)
    assert "udhcpc" in traced
    assert "http://trace.example/install" in traced
    assert not _first_line(traced).startswith("#cloud-config")


def test_playbooks_keep_the_acceptance_shape():
    deploy = (EXAMPLE / "deploy_nested_lab.yml").read_text(encoding="utf-8")
    discover = (EXAMPLE / "find_new_system.yml").read_text(encoding="utf-8")
    baseline = (EXAMPLE / "post_install.yml").read_text(encoding="utf-8")

    assert "ipaddress_type: none" in deploy
    assert "media_source:" in deploy
    assert "nested_virtualization: true" in deploy
    assert "bios_type: uefi" in deploy
    assert "boot_order: cd" in deploy
    assert "vergeio.vergeos.nic:" in deploy
    assert "vlab_node_count | int >= 2" in deploy
    assert "vlab_node_count | int >= 3" in deploy
    assert "user-data-node1.sh.j2" in deploy
    assert "user-data-node2.sh.j2" in deploy
    assert "user-data-node3.sh.j2" in deploy
    assert "no_log: true" in deploy

    assert "Login required" in discover
    assert "exclude_ips" in discover
    assert "discover_tries" in discover
    assert "retries:" in discover

    assert "vergeio.vergeos.tag_category:" in baseline
    assert "vergeio.vergeos.tag:" in baseline
    assert "taggable_vms: true" in baseline
