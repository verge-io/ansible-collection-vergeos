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

## A note on where that secret can be used

The secret this role produces works against the API — the verification step
proves it on every run. It **cannot currently be handed to the collection's
modules**, which take only `username`/`password`; the inventory plugin does
accept it, as its `api_key` option. That asymmetry is tracked as D5 in
`docs/DEFECTS-D1-D6.md`. So today this role is most useful for rotating keys
consumed by the inventory plugin, by `vrg`, or by anything else talking to the
API directly.

## Results

**Live ladder** — `tests/live/verify-key-rotation.yml`, passed on **VergeOS
26.1.8, two nodes** (`ok=57 changed=7 failed=0`):

an empty key name refused → the first rotation bootstraps one generation and
writes a `0600` secret file → the second rotation replaces it, leaving
exactly one → a deliberately colliding suffix is refused **without**
destroying the live generation → zero leftovers.
