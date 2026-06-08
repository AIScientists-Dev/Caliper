#!/usr/bin/env bash
# Caliper — transient EC2 for Stage-1 genomics runs.
#
# REVIEW BEFORE RUNNING. This creates BILLABLE AWS resources (a ~128 GB instance,
# ~$1/hr on-demand, plus EBS). Stop or terminate it when idle.
#
# Reads AWS creds/region from the environment (e.g. `set -a; source ~/.secrets/api_keys.env`).
# Secure-by-default: SSH (port 22) is locked to your current public IP only.
#
# Usage:
#   ./scripts/aws_ec2.sh launch     # create key+SG, launch instance, install miniforge
#   ./scripts/aws_ec2.sh setup      # rsync repo + build the conda env on the instance
#   ./scripts/aws_ec2.sh ssh        # open a shell on the instance
#   ./scripts/aws_ec2.sh status     # show instance state + public IP
#   ./scripts/aws_ec2.sh stop       # stop (keeps disk; cheap) -- restart later
#   ./scripts/aws_ec2.sh terminate  # destroy instance + its volume
set -euo pipefail

REGION="${REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
TYPE="${TYPE:-r6i.4xlarge}"          # 16 vCPU / 128 GiB
DISK_GB="${DISK_GB:-1000}"           # gp3 for FASTQ + genome + indices
NAME="${NAME:-caliper-bio}"
KEY="${KEY:-caliper-key}"
PEM="$HOME/.secrets/${KEY}.pem"
SG="${SG:-caliper-bio-sg}"
ENVFILE="${ENVFILE:-environments/bio-chip.yml}"   # Stage-1 ChIP runtime
ENVNAME="${ENVNAME:-caliper-bio-chip}"
AMI_PARAM="/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"

aws() { command aws --region "$REGION" "$@"; }
iid() { aws ec2 describe-instances --filters "Name=tag:Name,Values=$NAME" \
        "Name=instance-state-name,Values=pending,running,stopped,stopping" \
        --query 'Reservations[].Instances[].InstanceId' --output text; }
ip()  { aws ec2 describe-instances --instance-ids "$(iid)" \
        --query 'Reservations[].Instances[].PublicIpAddress' --output text; }

case "${1:-}" in
launch)
  MYIP="$(curl -s https://checkip.amazonaws.com)/32"
  AMI="$(aws ssm get-parameters --names "$AMI_PARAM" --query 'Parameters[0].Value' --output text)"
  echo "region=$REGION type=$TYPE disk=${DISK_GB}G ami=$AMI ssh-from=$MYIP"

  [ -f "$PEM" ] || { aws ec2 create-key-pair --key-name "$KEY" \
      --query 'KeyMaterial' --output text > "$PEM"; chmod 600 "$PEM"; echo "key -> $PEM"; }

  SGID="$(aws ec2 describe-security-groups --group-names "$SG" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
  if [ -z "$SGID" ] || [ "$SGID" = "None" ]; then
    SGID="$(aws ec2 create-security-group --group-name "$SG" \
        --description 'Caliper bio EC2' --query 'GroupId' --output text)"
  fi
  aws ec2 authorize-security-group-ingress --group-id "$SGID" \
      --protocol tcp --port 22 --cidr "$MYIP" 2>/dev/null || true

  # First-boot: install miniforge so `conda`/`mamba` are ready.
  USERDATA="$(printf '#!/bin/bash\ncurl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/mf.sh\nbash /tmp/mf.sh -b -p /opt/miniforge\n/opt/miniforge/bin/conda init bash\nchown -R ubuntu:ubuntu /opt/miniforge\n' | base64)"

  aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" \
    --key-name "$KEY" --security-group-ids "$SGID" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$DISK_GB,VolumeType=gp3}" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --user-data "$(echo "$USERDATA" | base64 --decode)" \
    --query 'Instances[0].InstanceId' --output text
  echo "launched. wait ~2 min, then: $0 setup"
  ;;
setup)
  HOST="ubuntu@$(ip)"
  echo "rsync repo -> $HOST"
  rsync -az -e "ssh -i $PEM -o StrictHostKeyChecking=accept-new" \
    --exclude .venv --exclude runs --exclude '__pycache__' \
    "$(cd "$(dirname "$0")/.." && pwd)/" "$HOST:~/Caliper/"
  ssh -i "$PEM" "$HOST" "source /opt/miniforge/etc/profile.d/conda.sh && \
    mamba env create -f ~/Caliper/$ENVFILE -y && \
    conda activate $ENVNAME && pip install -e ~/Caliper && \
    echo ENV_READY"
  ;;
ssh)        ssh -i "$PEM" "ubuntu@$(ip)" ;;
status)     aws ec2 describe-instances --instance-ids "$(iid)" \
              --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress,InstanceType]' \
              --output table ;;
stop)       aws ec2 stop-instances --instance-ids "$(iid)" ;;
terminate)  aws ec2 terminate-instances --instance-ids "$(iid)" ;;
*) echo "usage: $0 {launch|setup|ssh|status|stop|terminate}"; exit 1 ;;
esac
