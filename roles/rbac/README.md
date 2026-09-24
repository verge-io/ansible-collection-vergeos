# rbac

Groups, membership and permissions from one declarative document.

VergeOS answers *who can log in* through auth sources; nothing answered *what
they can do*. This role does, and it makes the answer reviewable in git.

## Usage

```yaml
- hosts: localhost
  gather_facts: false
  roles:
    - role: vergeio.vergeos.rbac
      vars:
        rbac_groups:
          - name: operators
            users: [alice, bob]
            permissions:
              - table: vms
                rights: [list, read, create, modify]   # not delete
              - table: vnets
                rights: [list, read]
```

## The model

VergeOS attaches permissions to an **identity**, which both users and groups
carry. Granting to a user and granting to a group are the same operation with a
different lookup — which is also why a permission read back from the API names
an identity rather than the thing you granted to.

Five rights: `list`, `read`, `create`, `modify`, `delete`. `full_control: true`
grants all five.

`table: "/"` is the system root, which is how a system-wide grant is expressed.
`row: 42` scopes a grant to a single row; `row: 0` (the default) is the whole
table.

## Things worth knowing

**Rights are the whole grant, not an addition.** A right you do not name is
denied. That makes the document the complete statement for the scopes it
mentions, so reading it tells you what access exists.

**A table-level grant and a row-level grant are different permissions.** Both
are matched exactly, on `table#row`. Comparing on the table alone would let a
declared row-level grant shelter an undeclared table-wide one — the direction
that silently keeps too much access.

**Additive by default, in two places.** `exact_members` per group and
`rbac_exact_permissions` globally. Both off, because a partial document that
silently removed everyone and everything it did not mention would be
destructive, and partial documents are the normal case while adopting an
estate. Adopt with them off, read the report, then flip.

**Exact permissions is scoped to the groups you name.** A group absent from
the document is left entirely alone, so exact mode narrows rights *within*
managed groups rather than purging the system.

**Prefer groups to users.** A permission granted to a group survives staff
changes; one granted to a user has to be found and removed when they leave.

**Platform groups are reported, never reconciled.** A document that quietly
rewrote them would be a surprising way to lose administrative access.

**Users inside a tenant are out of scope.** They need tenant-context
authentication — point the role at the tenant's own URL instead.
