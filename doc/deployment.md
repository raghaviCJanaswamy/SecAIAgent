# Deployment Guide

## Target environment

Production runs on **Amazon EKS** (Elastic Kubernetes Service). The container image is stored in **Amazon ECR** (Elastic Container Registry). No AWS Bedrock is used — the agent makes no outbound AI API calls.

---

## Prerequisites

| Tool | Purpose |
|---|---|
| `aws` CLI ≥ 2.x | ECR login, EKS context |
| `kubectl` ≥ 1.28 | Apply Kubernetes manifests |
| `docker` | Build container image |
| AWS Load Balancer Controller | ALB Ingress on EKS |
| ACM certificate | HTTPS for the ALB |

---

## Step 1 — Build and push to ECR

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1
export IMAGE_TAG=2.0.0
export ECR_REPO=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/xray-agent

# Create the ECR repository (once only)
aws ecr create-repository --repository-name xray-agent --region $AWS_REGION

# Authenticate Docker to ECR
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# Build for linux/amd64 (EKS node arch)
docker build --platform linux/amd64 -t $ECR_REPO:$IMAGE_TAG .

# Push
docker push $ECR_REPO:$IMAGE_TAG
```

---

## Step 2 — Update the image reference in deployment.yaml

Open [k8s/deployment.yaml](../k8s/deployment.yaml) and replace the placeholder image line:

```yaml
# Before:
image: xray-agent:2.0.0

# After:
image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/xray-agent:2.0.0
```

---

## Step 3 — Configure kubectl for EKS

```bash
aws eks update-kubeconfig --name your-cluster-name --region $AWS_REGION
kubectl get nodes   # verify connectivity
```

---

## Step 4 — Populate secrets

Secrets must be created before the Deployment is applied. Two options:

### Option A — kubectl (simple, not recommended for production)

```bash
kubectl create secret generic xray-agent-secrets \
  --namespace xray-agent \
  --from-literal=GITHUB_TOKEN='ghp_...' \
  --from-literal=XRAY_WEBHOOK_SECRET='your-shared-secret' \
  --from-literal=JFROG_URL='https://yourinstance.jfrog.io' \
  --from-literal=JFROG_USERNAME='your-username' \
  --from-literal=JFROG_PASSWORD='your-password'
```

### Option B — AWS Secrets Manager + External Secrets Operator (recommended)

1. Store secrets in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name xray-agent/prod \
  --secret-string '{
    "GITHUB_TOKEN": "ghp_...",
    "XRAY_WEBHOOK_SECRET": "your-shared-secret",
    "JFROG_URL": "https://yourinstance.jfrog.io",
    "JFROG_USERNAME": "your-username",
    "JFROG_PASSWORD": "your-password"
  }'
```

2. Install External Secrets Operator and create an `ExternalSecret` resource pointing to the ARN above. The operator will sync the values into a Kubernetes Secret named `xray-agent-secrets` automatically.

---

## Step 5 — Apply Kubernetes manifests

Apply in this order (dependencies: namespace first, then everything else):

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/secret.yaml          # skip if using External Secrets Operator
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/poddisruptionbudget.yaml
```

Or apply the whole directory at once:

```bash
kubectl apply -f k8s/
```

---

## Step 6 — Configure the ALB Ingress

The [k8s/ingress.yaml](../k8s/ingress.yaml) exposes the agent via an internal AWS ALB. Before applying, update two placeholders:

```yaml
# ACM certificate for HTTPS
alb.ingress.kubernetes.io/certificate-arn: "arn:aws:acm:us-east-1:123456789012:certificate/xxxx"

# Your internal domain (Route 53 or split-horizon DNS)
- host: xray-agent.internal.yourcompany.com
```

Exposed paths:
- `HTTPS /health` — liveness/readiness probe
- `HTTPS /webhook` — JFrog Xray webhook receiver

The ALB is configured as **internal** (not internet-facing). Inbound traffic is restricted to `10.0.0.0/8` (VPC CIDR). Update `alb.ingress.kubernetes.io/inbound-cidrs` if your VPC uses a different range.

---

## Step 7 — Verify the deployment

```bash
# All pods running?
kubectl get pods -n xray-agent

# Deployment status
kubectl rollout status deployment/xray-agent -n xray-agent

# Check logs
kubectl logs -n xray-agent -l app.kubernetes.io/name=xray-agent --tail=50

# Health check via ALB (once DNS is configured)
curl https://xray-agent.internal.yourcompany.com/health
```

---

## Kubernetes manifest summary

| File | Resource | Purpose |
|---|---|---|
| [k8s/namespace.yaml](../k8s/namespace.yaml) | Namespace | Isolated `xray-agent` namespace |
| [k8s/serviceaccount.yaml](../k8s/serviceaccount.yaml) | ServiceAccount | Non-default SA; IRSA annotation slot for ECR/Secrets Manager |
| [k8s/secret.yaml](../k8s/secret.yaml) | Secret | Credentials template (or replaced by External Secrets Operator) |
| [k8s/configmap.yaml](../k8s/configmap.yaml) | ConfigMap | Non-sensitive settings (labels, severity threshold) |
| [k8s/deployment.yaml](../k8s/deployment.yaml) | Deployment | 2 replicas, AZ spread, read-only FS, non-root user |
| [k8s/service.yaml](../k8s/service.yaml) | Service | ClusterIP — internal only |
| [k8s/hpa.yaml](../k8s/hpa.yaml) | HorizontalPodAutoscaler | Scale 2–5 replicas on CPU>60% / memory>75% |
| [k8s/ingress.yaml](../k8s/ingress.yaml) | Ingress | AWS ALB (internal, HTTPS only, VPC-restricted) |
| [k8s/poddisruptionbudget.yaml](../k8s/poddisruptionbudget.yaml) | PodDisruptionBudget | minAvailable=1 during node drains/upgrades |

---

## Container security posture

The production container enforces the following controls:

| Control | Setting |
|---|---|
| User | Non-root uid/gid 1001 |
| Root filesystem | Read-only (`readOnlyRootFilesystem: true`) |
| Linux capabilities | All dropped (`drop: ["ALL"]`) |
| Privilege escalation | Disabled (`allowPrivilegeEscalation: false`) |
| Seccomp | `RuntimeDefault` profile |
| Writable volume | `/tmp` only, via `emptyDir` |
| Token automount | Disabled (`automountServiceAccountToken: false`) |

---

## Resource sizing

| | CPU | Memory |
|---|---|---|
| Request | 250m | 128Mi |
| Limit | 500m | 256Mi |

The agent is I/O-bound (GitHub API + PyPI HTTP calls), not CPU-bound. These limits are conservative. Monitor actual usage after go-live and adjust.

---

## Rolling updates

```bash
# Tag and push new image
docker build --platform linux/amd64 -t $ECR_REPO:2.1.0 .
docker push $ECR_REPO:2.1.0

# Update image in deployment
kubectl set image deployment/xray-agent \
  xray-agent=$ECR_REPO:2.1.0 \
  -n xray-agent

# Monitor rollout
kubectl rollout status deployment/xray-agent -n xray-agent

# Rollback if needed
kubectl rollout undo deployment/xray-agent -n xray-agent
```
