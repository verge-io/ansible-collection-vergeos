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
both `no_log`. Take the token from a vault, not a file in the repo. The
ladder asserts the token has not leaked into a readable VM field.

## Guest internet required

The k3s install runs in-guest against `get.k3s.io`. A node on an isolated
network will boot and then sit there.

## Results

- **14 renderer unit tests** — `tests/unit/roles/test_k3s_node_render.py`,
  including shell-safe token quoting
- **Live ladder** — `tests/live/verify-k3s-node.yml`, passed on **VergeOS
  26.1.8, two nodes** (`ok=54 changed=7 failed=0`):

  a spec with no role refused → an agent with no `server_url` refused → both
  refusals side-effect free → a server node clones, boots with the declared
  specs, gets one NIC and a `nocloud` cloud-init document → the token did not
  leak → a second run **adopts** rather than cloning again → zero leftovers.

The ladder verifies the **deployment**, not cluster convergence. Proving a
node reached `Ready` means waiting on an in-guest install over the internet;
that is a longer and flakier test, and it belongs in a cluster-level ladder
rather than in the per-node one.

## Prerequisite

A golden template with a snapshot, as produced by `image_pipeline`.
