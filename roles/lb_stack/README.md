# lb_stack

An haproxy load balancer, deployed and configured as code.

## Honest label

**VergeOS has no NSX-ALB-style fabric load balancer.** This role does not
create one. What you get is a VM running haproxy (optionally with keepalived
for a VIP), built from a declarative spec — and **you operate it**. Patching
it, monitoring it, and failing it over are yours. It does not become a
platform service because Ansible created it.

That is a real mitigation, and it is worth having. It is just not parity, and
this README will not imply that it is.

## How it composes

```
image_pipeline golden template
  -> vm_clone
  -> lb_render.py: spec -> haproxy.cfg + keepalived.conf + cloud-init
  -> cloud_init
  -> nic
  -> boot
```

`files/lb_render.py` is a pure function from spec to configuration, which is
what makes it testable without deploying anything.

## The spec

```yaml
lb_stack_spec:
  hostname: lb1
  frontends:
    - {name: web, bind_port: 80, default_backend: app}
  backends:
    - name: app
      httpchk: true
      servers:
        - {name: a, host: 192.0.2.10, port: 8081}
        - {name: b, host: 192.0.2.11, port: 8081}
  ha:
    vip: 192.0.2.100/24
    state: MASTER
    priority: 200
    router_id: 77
    auth_pass: "{{ vault_lb_vrrp_pass }}"
```

The `ha` block is optional; including it adds keepalived. For an
active/backup pair, run the role twice with different names and
`state: MASTER` / `state: BACKUP` at distinct priorities.

### `auth_pass` is required, and used not to be

It used to carry a hard-coded default. A default password in a published
repository is the same password on every HA pair that never overrode it, and
VRRP carries it in clear on the wire — so the only safe default is no
default.

The renderer refuses an `ha` block without `auth_pass`, and a test reads the
source with `ast` and asserts that `ha.get('auth_pass')` is called with **no
fallback argument at all** — which forbids any default rather than one
particular string.

The old value is not repeated in this repository. **If you have an HA pair
that predates this change and never set `auth_pass`, treat its VRRP secret
as known and rotate it.**

`router_id` still has a default, because keepalived requires one. It must be
unique per VRRP domain: two unrelated pairs left at 51 on the same L2 will
fight over each other's VIP.

### And the whole spec is now `no_log`

The rendered document carries that password. `k3s_node` marked its render and
its cloud-init task `no_log` for its cluster token; this role, four files
away, marked neither, so the VRRP secret went to the log on `-vvv` or on any
task failure. Both are marked now, and the spec travels to the renderer on
stdin rather than argv so it never reaches a process listing.

## Usage

```yaml
- role: vergeio.vergeos.lb_stack
  vars:
    lb_stack_name: lb1
    lb_stack_template: debian13-golden
    lb_stack_network: External
    lb_stack_spec: "{{ my_lb_spec }}"
```

The spec is rendered **before** anything is created, so a spec that cannot
render does not leave a half-built VM behind.

## Check mode

`--check` renders the configuration and deploys nothing, which makes a dry
run genuinely useful: you can read the haproxy config a spec produces before
any of it reaches a cluster.

It did not, before. The render is a `command`, so check mode skipped it — and
a skipped command still registers an **empty** stdout rather than none at
all, so nothing crashed. The role carried on, attached an empty cloud-init
document, and reported success. A VM that boots with no configuration looks
exactly like a VM that booted.

`lb_stack_result.deployed` says whether a VM actually exists at the end of
the run. `cloned` no longer keys off `vm_clone` alone, because `vm_clone`
reports the clone it *would* have made under check mode.

## Results

- **60 renderer unit tests** — `tests/unit/roles/test_lb_stack_render.py`
- **Live ladder** — `tests/live/verify-lb-stack.yml`, passed on **VergeOS
  26.1.8, two nodes** (`ok=82 changed=8 failed=0`):

  an empty spec refused side-effect free → an `ha` block with no `auth_pass`
  refused → the LB clones from the golden template, boots with the declared
  specs, gets exactly one NIC and a `nocloud` cloud-init document → **and
  that document, read back from the platform, is the haproxy config we
  rendered, parses as YAML, and carries no keepalived block we did not ask
  for** → a second run **adopts** rather than cloning again → check mode
  renders but builds nothing → zero leftovers.

The unit tests stopped asserting on the rendered *bytes*. They used to read

```python
assert 'hostname: lb1' in ud
assert '  - touch /tmp/done' in ud
```

which is a claim about text, and every one of them passed while the renderer
emitted bare YAML scalars. Measured with a real parser: a hostname of
`lb1: x` made the document unparseable, `{lb}` parsed as a **mapping**, and
`extra_runcmd: ['echo: hi']` became a mapping inside `runcmd` — a valid
document that uploads, boots, and never runs the command. Cloud-init reads a
parsed document, so that is what the tests assert on now.

The ladder also stopped at the datasource. A VM with an **empty** user-data
has datasource `nocloud` too, which is exactly what check mode produced. The
document is what the role exists to deliver, so the document is what gets
read back.

The ladder verifies the deployment. It does not wait for haproxy to come up
and serve traffic — that needs backends to exist and adds minutes of guest
boot to every run. If you want that proof, point the spec at two real
backends and curl the frontend.

## A bug this role found in `vm`

Its adoption rung — run the role twice, the second must not clone again —
failed with

```
API error: Error starting machine: Machine is already running with status 'running'
```

`vm`'s `state: running` was never idempotent. `power_on_vm()` returns early
when the row says the VM is running, and the row never said so: the module
fetches an explicit field list, and neither `running` nor `status` survives
one. Fixed in this collection; see `plugins/modules/vm.py`.

## Prerequisite

A golden template with a snapshot to clone from, as produced by
`image_pipeline`. The ladder creates the snapshot if it is missing rather
than assuming lab state.
