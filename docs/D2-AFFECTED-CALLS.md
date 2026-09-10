# Which calls must bypass `_handle_response`

The question behind D2: pyvergeos discards the body of any response whose
status is not 200, 201 or 204. **Which calls does that actually cost us?**

Answered by measurement against a live VergeOS 26.1.8 system, not by
inference. Method and its limits are at the bottom.

---

## Short answer

**One confirmed call. One likely. And one latent trap that nothing currently
trips.**

| | Call | Status | What is lost |
|---|---|---|---|
| **1** | `POST vm_recipe_instances` with `simulate: true` | **405** | `response.logs` (28 entries), `response.answers`, `response.cloudinit_files` — the whole simulation report |
| **2** | `POST tenant_recipe_instances` with `simulate: true` | ? | Presumed identical. **Untested** — no tenant recipes exist on the lab |
| **3** | anything returning **202 Accepted** | 202 | Entire body. Nothing observed returns 202 on 26.1.8, but the code treats it as an error |

Everything else measured is unaffected. The scope of D2 is genuinely narrow.

---

## What is NOT affected — measured, not assumed

### Every table read is fine
All **322** tables fetched with `GET /api/v4/<table>?fields=most`:

```
non-2xx GET responses: 0 of 322 tables
```

No read anywhere in the API needs to bypass `_handle_response`.

### Error responses lose nothing
All **46** `*_actions` endpoints probed with a malformed body:

| status | count | body |
|---|---|---|
| 422 | 41 | `{'err': "field '<table>.<field>' is required"}` |
| 201 | 4 | `{'$key', '$row', 'dbpath', 'location'}` — success |
| 404 | 1 | `{'err': 'Error querying user: ...'}` |

Every error body is `{'err': '...'}` and nothing else. `_extract_error_message`
already pulls `err` out, so **for genuine errors the current behaviour loses
no information.** This matters: D2 is not "error handling is lossy in
general". It is specific to a success reported on a non-success status.

### A real recipe deploy is fine
Only the *simulate* answers 405. A real `POST vm_recipe_instances` returns
2xx and flows through normally — confirmed by a live deploy that returned
`vm_key=36`.

---

## The latent trap: 202 Accepted

```python
# constants.py:75-81
HTTP_SUCCESS_CODES = frozenset({
    HTTPStatus.OK,       # 200
    HTTPStatus.CREATED,  # 201
})
HTTP_NO_CONTENT = HTTPStatus.NO_CONTENT  # 204
```

That is the complete success set. **202 Accepted is handled as an error** and
would raise `APIError` with its body discarded — the standard REST reply for
"I have accepted your long-running request" is treated as a failure.

Nothing in anything measured returns 202. Observed statuses across 322 GETs,
46 action POSTs and the recipe create/simulate paths were only:

```
200, 201, 404, 405, 422
```

So this is a fragility rather than a live bug. It is worth fixing in the same
change as D2, because the fix is the same shape: stop deciding what a status
*means* on the caller's behalf.

---

## Recommended fix

One change covers all three rows of the table at the top:

```python
# client.py, in _handle_response, before raising
except APIError as exc:
    exc.body = parsed_body   # may be None
    raise
```

and add `202` to `HTTP_SUCCESS_CODES`.

Verified by applying the body change at runtime and re-running the identical
SDK call:

```
before:  str(e)='Simulation complete'  status=405  attrs=[args, status_code]
after :  status=405  err='Simulation complete'  log entries=28
         cloudinit=['/meta-data', '/network-config', '/user-data']
```

`str(e)` and `e.status_code` are unchanged, so no existing caller breaks.

---

## Method, and where it stops

**What was done, all read-only or reversible:**

1. `GET /api/v4/<table>?fields=most` for all 322 tables.
2. `POST` an empty body `{}` to all 46 `*_actions` endpoints.
3. `POST vm_recipe_instances` with `simulate: true` — a genuine dry run,
   confirmed to create nothing.
4. Inspected `HTTP_SUCCESS_CODES` directly.

**Where this stops, stated plainly.** The D2 pattern is *success reported on
a non-success status*. Finding those exhaustively would mean **successfully
executing** every action — rebooting nodes, deleting snapshots, restoring
volumes. That was not done, so:

> This list is complete for reads and for malformed requests. It is **not**
> guaranteed complete for successful writes. Two of the three rows above are
> confirmed by measurement; row 2 is inferred from an identical table shape
> and row 3 from reading the constants.

The honest way to close that gap is for whoever owns the API to say which
endpoints intentionally answer on a non-2xx status. A generated OpenAPI
document would answer it in seconds — `swagger_actions` reports having
generated one (`action: generate, status: complete`), but it is not reachable
at `/swagger`, `/openapi.json`, `/api-docs` or via the `files` table.

### A safety note for anyone repeating step 2

An empty `POST {}` to an action endpoint is **not** universally safe. Four of
the 46 accepted it and created a row with a default action:

```
billing_actions, clone_iso_actions, shared_object_actions, update_actions  -> 201
```

Three were transient. `clone_iso_actions` left a persistent row
(`{'$key': 1, 'action': 'update', 'params': []}`) which had to be deleted.

Checked immediately afterwards and nothing had actually run —
`update_settings` was unchanged (`installed=True, reboot_required=False,
applying_updates=False`) and both nodes were still out of maintenance. But
`update_actions` returning 201 to an empty body is uncomfortably close to
triggering a cluster update by accident. **Probe individual action endpoints
deliberately, not in a loop.**
