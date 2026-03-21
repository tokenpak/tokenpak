# TokenPak — AWS ECS (Fargate)

Deploy TokenPak on AWS ECS with Fargate (serverless containers).

## Prerequisites

- AWS CLI configured (`aws configure`)
- ECR repository created
- ECS cluster and VPC with subnets ready
- AWS Secrets Manager secrets for API keys

## Setup Steps

### 1. Push Image to ECR

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t tokenpak .
docker tag tokenpak:latest ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest
docker push ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest
```

### 2. Store API Keys in Secrets Manager

```bash
aws secretsmanager create-secret \
  --name tokenpak/anthropic-api-key \
  --secret-string "sk-ant-..." \
  --region us-east-1

aws secretsmanager create-secret \
  --name tokenpak/openai-api-key \
  --secret-string "sk-..." \
  --region us-east-1
```

### 3. Create CloudWatch Log Group

```bash
aws logs create-log-group --log-group-name /ecs/tokenpak --region us-east-1
```

### 4. Register Task Definition

Edit `task-definition.json` to replace `ACCOUNT_ID` with your AWS account ID, then:

```bash
aws ecs register-task-definition \
  --cli-input-json file://task-definition.json \
  --region us-east-1
```

### 5. Create ECS Service

```bash
aws ecs create-service \
  --cluster tokenpak-cluster \
  --service-name tokenpak \
  --task-definition tokenpak \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}" \
  --region us-east-1
```

## View Logs

```bash
# Stream live logs
aws logs tail /ecs/tokenpak --follow --region us-east-1
```

## Update Image

```bash
# After pushing new image:
aws ecs update-service \
  --cluster tokenpak-cluster \
  --service tokenpak \
  --force-new-deployment \
  --region us-east-1
```

## Notes

- API keys are stored in Secrets Manager (not environment variables)
- CloudWatch Logs captures all proxy output
- Fargate handles infrastructure — no EC2 instances to manage
- Add a Load Balancer (ALB) to expose the proxy with a stable hostname
- Replace `ACCOUNT_ID` throughout with your 12-digit AWS account ID
