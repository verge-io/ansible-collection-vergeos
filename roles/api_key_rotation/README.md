# api_key_rotation

Rotate an API key without locking yourself out.

## Verify before revoke

Each run, in this order:

1. create a new generation, `<key_name>-<suffix>`
2. **prove the new secret authenticates** with a live `Authorization: Bearer`
   request
3. only then delete every older generation
4. optionally write the new secret to disk at `0600`

Step 2 sitting between the additive half and the destructive half is the
entire point. A rotation that revokes the old credential before confirming
the new one works is how automation locks itself out of the thing it
automates. **A failed verification leaves the old key untouched** and stops
the play.

`api_key_rotation_verify_bearer: false` exists for controllers that cannot
reach the API directly over HTTPS — but understand that it removes the
property this role is for.

## Suffix collisions are refused

The default suffix is a second-granular timestamp. Two rotations inside the
same second would produce the same generation name, so the role **refuses**
rather than quietly reusing one and leaving you believing a rotation
happened. The ladder proves the refusal is side-effect free: the live
generation is still there afterwards.

## Usage

```yaml
- role: vergeio.vergeos.api_key_rotation
  vars:
    api_key_rotation_user: automation
    api_key_rotation_key_name: ansible-runner
    api_key_rotation_secret_dest: /root/.vergeos_token
    api_key_rotation_ip_allow_list: ["192.0.2.0/24"]
```

Consumers can read the new secret from the role instead of a file:

```yaml
    api_key_rotation_secret        # the new secret
    api_key_rotation_new_name      # the new generation's name
    api_key_rotation_rotated_out   # the generations that were removed
```

All three are `no_log`.

## Where that secret can be used

Everywhere. The role's verification step proves the secret against the API on
every run, and every module in this collection accepts it as `api_key` (or
`VERGEOS_API_KEY`), as does the inventory plugin.

```yaml
- vergeio.vergeos.network_info:
    host: "{{ vergeos_host }}"
    api_key: "{{ api_key_rotation_secret }}"
```

When both `api_key` and `username`/`password` are set, the key wins — the
SDK's `connect()` prefers token auth, which also bypasses TOTP on tenants that
require it for password logins.

> This section previously said the opposite: that the secret could not be
> handed to the modules, only to the inventory plugin. That was true of an
> earlier branch and is not true here — measured on 26.1.8, `network_info`
> authenticating with nothing but a freshly rotated key returned all 13
> networks. The claim, and its citation to a document that is not in this
> repository, were both removed rather than carried forward.

## Results

**Live ladder** — `tests/live/verify-key-rotation.yml`, passed on **VergeOS
26.1.8, two nodes** (`ok=57 changed=7 failed=0`):

an empty key name refused → the first rotation bootstraps one generation and
writes a `0600` secret file → the second rotation replaces it, leaving
exactly one → a deliberately colliding suffix is refused **without**
destroying the live generation → zero leftovers.
