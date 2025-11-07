# Quick Start Guide From Tar File

This guide will help you get started with the VergeOS Ansible collection in minutes.

## Installation

```bash
ansible-galaxy collection build --force
ansible-galaxy collection install vergeio-vergeos-*.tar.gz --force
```

## Basic Setup

### 1. Set Environment Variables

The easiest way to authenticate is using environment variables:

```bash
export VERGEOS_HOST="your-vergeos-host.example.com"
export VERGEOS_USERNAME="your-username"
export VERGEOS_PASSWORD="your-password"
export VERGEOS_INSECURE="false"  # Set to "true" for self-signed certificates
```
### 2. Examples Windows and RHEL VM from OVA with hostname and static ip set

Upload ova files to VergeOS

Configure static var examples in 
RHEL) examples/import_and_configure_vm.yml (ova cloud-init installed and enabled)
WINDOWS) examples/import_and_configure_winvm.yml (ova sysprep /generalize /oobe /shutdown)

Configure and Run Playbooks
```bash
ansible-playbook examples/import_and_configure_winvm.yml
ansible-playbook examples/import_and_configure_vm.yml
```

### 3. Example VM snapshot workflow

Configure and Run Playbook
```bash
ansible-playbook examples/snapshot_workflow.yml

```
