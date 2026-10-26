# Deploying on AWS EC2 with the AWS CLI

One EC2 instance runs two containers from `deploy/aws/compose.yaml`: Interra
(the page and both LiveKit workers, under `agent.serve`) and Caddy, which gets
an HTTPS certificate automatically. The browser connects straight to LiveKit
Cloud for audio and video, so the instance only needs ports 80 and 443 open.
No domain is required: `<ip-with-dashes>.sslip.io` resolves to the instance's address.

Run the commands on your machine (WSL) unless a step says **on the server**.
Region `ap-south-1` (Mumbai) is closest to the LiveKit project in India South.

## 1. Install the AWS CLI

```bash
sudo dnf install -y unzip            # Ubuntu/Debian: sudo apt install -y unzip
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
unzip -q -o /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update
aws --version
```

## 2. Give the CLI credentials

Do not create access keys for the root account. In the AWS console:

1. **IAM → Users → Create user** named `interra-cli`. Choose **Attach policies
   directly** and select `AmazonEC2FullAccess`.
2. Open the user, then **Security credentials → Create access key →
   Command Line Interface (CLI)**. Copy the key ID and the secret.

```bash
aws configure
# AWS Access Key ID:     <key id>
# AWS Secret Access Key: <secret>
# Default region name:   ap-south-1
# Default output format: json
aws sts get-caller-identity          # prints the account and user if it worked
```

The keys are stored in `~/.aws/credentials`. Keep that file out of the repository.

## 3. Pick an instance type your credits cover

```bash
aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true \
  --query "sort_by(InstanceTypes,&MemoryInfo.SizeInMiB)[].[InstanceType,VCpuInfo.DefaultVCpus,MemoryInfo.SizeInMiB]" \
  --output table
```

The page plus the three workers need about 2–2.5 GB of memory. Pick the first
listed type with **4096 MiB or more** (often `c7i-flex.large` or
`m7i-flex.large`). If the largest listed is `t3.small` (2 GB), use it: step 7
adds swap.

## 4. Create the key pair, firewall and instance

Set the variables once. Step 5 saves them, so you can reload them later with `source ~/.interra-aws`.

```bash
export AWS_REGION=ap-south-1
TYPE=c7i-flex.large                  # from step 3
MYIP=$(curl -s https://checkip.amazonaws.com)

# SSH key
aws ec2 create-key-pair --key-name interra --key-type ed25519 \
  --query KeyMaterial --output text > ~/.ssh/interra.pem
chmod 400 ~/.ssh/interra.pem

# Firewall: SSH from your address only, web from anywhere
SG=$(aws ec2 create-security-group --group-name interra-web \
  --description "Interra web and SSH" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$MYIP/32"
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 443 --cidr 0.0.0.0/0

# Latest Ubuntu 24.04 image from Canonical
AMI=$(aws ec2 describe-images --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" "Name=state,Values=available" \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)

# The instance, with a 20 GB disk
ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name interra \
  --security-group-ids "$SG" \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=20,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=interra}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$ID"
```

If `run-instances` says the type is not eligible, go back to step 3 and pick
another type from the list.

## 5. Give it a fixed address

Without an Elastic IP, the address changes every time the instance is stopped,
and so does the HTTPS name.

```bash
ALLOC=$(aws ec2 allocate-address --query AllocationId --output text)
aws ec2 associate-address --instance-id "$ID" --allocation-id "$ALLOC"
IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC" --query 'Addresses[0].PublicIp' --output text)

cat > ~/.interra-aws <<EOF
export AWS_REGION=$AWS_REGION ID=$ID SG=$SG ALLOC=$ALLOC IP=$IP
EOF
echo "https://${IP//./-}.sslip.io"
```

## 6. Connect

```bash
ssh -i ~/.ssh/interra.pem ubuntu@"$IP"
```

The first connection can be refused for about 30 seconds while the instance boots.

## 7. Install Docker and start Interra (on the server)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu

# Swap: required on 2 GB instances, harmless on larger ones
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

exit                                 # log out so the docker group applies
```

Reconnect with step 6, then:

```bash
git clone https://github.com/Maitri-shah29/Interra.git
cd Interra/deploy/aws
cp interra.env.example interra.env && chmod 600 interra.env
nano interra.env                     # LiveKit URL/key/secret and the admin password
echo "SITE_ADDRESS=$(curl -s https://checkip.amazonaws.com | tr . -).sslip.io" > .env
docker compose up -d --build         # the first build takes a few minutes
docker compose logs -f               # Ctrl+C stops following, not the app
```

Look for `[serve] page started` and `registered worker` three times: for
`interra-benchmark`, `interra-camera-troubleshooter` and `interra-home`.

## 8. Open it

Go to `https://<ip-with-dashes>.sslip.io`, the address step 5 printed. The certificate
can take up to a minute the first time. Change settings at `/settings` (see
`docs/DEPLOY.md`). Saving there restarts the workers; no SSH is needed.

## Day to day

Update to the latest code (on the server):

```bash
cd ~/Interra && git pull && cd deploy/aws && docker compose up -d --build
```

Stop the instance when nobody needs it. That stops compute charges; the disk
and the Elastic IP still cost a little. The containers come back on start.

```bash
source ~/.interra-aws
aws ec2 stop-instances --instance-ids "$ID"
aws ec2 start-instances --instance-ids "$ID"
aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].State.Name' --output text
```

If your own IP changes and SSH stops connecting:

```bash
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "$(curl -s https://checkip.amazonaws.com)/32"
```

Check what you are spending under **Billing and Cost Management → Credits**.
Set a budget alert there as well.

## Remove everything

```bash
source ~/.interra-aws
aws ec2 terminate-instances --instance-ids "$ID"
aws ec2 wait instance-terminated --instance-ids "$ID"
aws ec2 release-address --allocation-id "$ALLOC"
aws ec2 delete-security-group --group-id "$SG"
aws ec2 delete-key-pair --key-name interra && rm -f ~/.ssh/interra.pem
```

## Troubleshooting

- **Page does not load over HTTPS:** run `docker compose logs caddy`. Ports 80
  and 443 must be open (step 4), and `.env` must hold the instance's current address.
- **The voice, camera or home worker keeps restarting:** run `docker compose logs interra`.
  A missing LiveKit variable is the usual cause; fix it in `/settings` or `interra.env`.
- **`worker is at full capacity`:** raise `INTERRA_AGENT_LOAD_THRESHOLD` in `/settings`.
- **Build killed or very slow on 2 GB:** check the swap with `free -h`.
