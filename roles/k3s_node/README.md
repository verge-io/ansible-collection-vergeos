# k3s_node

One k3s Kubernetes node as a VM.

## Honest label

**VergeOS has no Tanzu or NKE equivalent.** This is Kubernetes you operate.
Upgrades, etcd health, certificate rotation and capacity are all yours, and
deploying it from Ansible does not change that. It is DIY k8s with a
repeatable build, which is genuinely useful and is not a managed service.

## One node per invocation

A cluster is a playbook that runs this role once per node with a shared
token:

```yaml
- hosts: localhost
  tasks:
    - name: Control plane
      ansible.builtin.include_role:
        name: vergeio.vergeos.k3s_node
      vars:
        k3s_node_name: k3s-server
        k3s_node_template: debian13-golden
        k3s_node_spec:
          role: server
          token: "{{ vault_k3s_token }}"
          disable: [traefik]

    - name: Workers
      ansible.builtin.include_role:
        name: vergeio.vergeos.k3s_node
      vars:
        k3s_node_name: "k3s-agent-{{ item }}"
        k3s_node_template: debian13-golden
        k3s_node_spec:
          role: agent
          token: "{{ vault_k3s_token }}"
          server_url: "https://{{ server_ip }}:6443"
      loop: [1, 2]
```

An agent with no `server_url` is **refused** — without it the node boots,
installs k3s, and joins nothing, which is the kind of failure you discover
much later.

## Secrets

The spec carries the cluster token, so the render and the cloud-init task are
both `no_log`, and the spec reaches the renderer on **stdin** rather than
argv so it never appears in a process listing. Take the token from a vault,
not a file in the repo.

The ladder asserts both halves: the token **is** in the cloud-init document
the platform stored — it has to be, or the node can never join anything —
and it is in no readable VM field.

## Nothing in the spec can start a second command

The renderer already reached for `shlex.quote` on the environment values and
not on the install arguments, four lines apart. So

```yaml
tls_sans: ['k8s.example.com; touch /tmp/pwned']
```

rendered as

```
sh -s - server --tls-san k8s.example.com; touch /tmp/pwned
```

which cloud-init runs as root in the guest. A spec assembled from inventory,
a CMDB, or a recipe answer is not a trusted string. Everything that reaches
that shell line is quoted now — arguments, token, server URL and the
`phone_home` callback — and the tests check it the way a shell would, by
splitting the rendered line and requiring the payload to survive as exactly
one word.

## Check mode

`--check` renders the cloud-init and deploys nothing, so a dry run shows you
the install line a spec produces.

It did not, before. The render is a `command`, so check mode skipped it — and
a skipped command still registers an **empty** stdout rather than none at
all, so nothing crashed. The role carried on and attached an empty cloud-init
document. A node that boots with no cloud-init looks exactly like a node that
booted.

`k3s_node_result.deployed` says whether a VM actually exists at the end of
the run. `cloned` no longer keys off `vm_clone` alone, because `vm_clone`
reports the clone it *would* have made under check mode.

## Guest internet required

The k3s install runs in-guest against `get.k3s.io`. A node on an isolated
network will boot and then sit there.

## Results

- **77 renderer unit tests** — `tests/unit/roles/test_k3s_node_render.py`,
  including a shell-injection matrix over every value that reaches the
  install line
- **Live ladder** — `tests/live/verify-k3s-node.yml`, passed on **VergeOS
  26.1.8, two nodes** (`ok=80 changed=8 failed=0`):

  a spec with no role refused → an agent with no `server_url` refused → both
  refusals side-effect free → a server node clones, boots with the declared
  specs, gets one NIC and a `nocloud` cloud-init document → **and that
  document, read back from the platform, is the k3s install we rendered,
  parses as YAML, carries the cluster token, and carries no `K3S_URL` a
  server should not have** → the token leaked into no readable VM field → a
  second run **adopts** rather than cloning again → check mode renders but
  builds nothing → zero leftovers.

The ladder used to stop at the datasource. A VM with an **empty** user-data
has datasource `nocloud` too, which is exactly what check mode produced. It
also called a helper — `docs/repro/d1-d6/readback.py` — that does not exist
in this repository, the same defect as #32 and #44; the guard written for
those caught it on import, in both ladders.

The ladder verifies the **deployment**, not cluster convergence. Proving a
node reached `Ready` means waiting on an in-guest install over the internet;
that is a longer and flakier test, and it belongs in a cluster-level ladder
rather than in the per-node one.

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

A golden template with a snapshot, as produced by `image_pipeline`.
