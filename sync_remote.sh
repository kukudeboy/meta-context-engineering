#!/bin/bash
echo "==> Triggering remote git pull on ECS..."
workbench exec --instance-id i-wz94cnv67bzrhlntpnxi --command "cd /root/meta-context-engineering && git pull origin main"
echo "==> Sync complete!"
