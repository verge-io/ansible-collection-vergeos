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
  ha: {vip: 192.0.2.100/24, state: MASTER, priority: 200}
```

The `ha` block is optional; including it adds keepalived. For an
active/backup pair, run the role twice with different names and
`state: MASTER` / `state: BACKUP` at distinct priorities.

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

## Results

- **14 renderer unit tests** — `tests/unit/roles/test_lb_stack_render.py`,
  including YAML-validity of the emitted cloud-config
- **Live ladder** — `tests/live/verify-lb-stack.yml`, passed on **VergeOS
  26.1.8, two nodes** (`ok=49 changed=8 failed=0`):

  an empty spec refused side-effect free → the LB clones from the golden
  template, boots with the declared specs, gets exactly one NIC and a
  `nocloud` cloud-init document → a second run **adopts** rather than cloning
  again → zero leftovers.

The ladder verifies the deployment. It does not wait for haproxy to come up
and serve traffic — that needs backends to exist and adds minutes of guest
boot to every run. If you want that proof, point the spec at two real
backends and curl the frontend.

## Prerequisite

A golden template with a snapshot to clone from, as produced by
`image_pipeline`. The ladder creates the snapshot if it is missing rather
than assuming lab state.
