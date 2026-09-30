#!/bin/bash
echo "==> Packaging local changes..."
# Only package tracked files and avoid transferring the whole .git history
git archive --format=tar.gz -o /tmp/sync_patch.tar.gz HEAD

echo "==> Uploading to ECS..."
workbench upload /tmp/sync_patch.tar.gz /tmp/sync_patch.tar.gz --instance-id i-wz94cnv67bzrhlntpnxi

echo "==> Applying on ECS..."
workbench exec --instance-id i-wz94cnv67bzrhlntpnxi --command "tar -xzf /tmp/sync_patch.tar.gz -C /root/meta-context-engineering && rm -f /tmp/sync_patch.tar.gz"

rm -f /tmp/sync_patch.tar.gz
echo "==> Direct sync complete!"
