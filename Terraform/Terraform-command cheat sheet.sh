# Initialize Terraform
terraform init

# Format code
terraform fmt -recursive

# Validate configuration
terraform validate

# Plan changes
terraform plan -out=tfplan

# Apply changes
terraform apply tfplan

# Destroy infrastructure
terraform destroy

# View state
terraform show
terraform state list

# Workspace management
terraform workspace new dev
terraform workspace select dev
terraform workspace list

# Import existing resource
terraform import aws_instance.bot i-1234567890abcdef0

# Refresh state
terraform refresh

# Taint resource (mark for recreation)
terraform taint aws_instance.bot[0]

# Output values
terraform output
terraform output bot_alb_dns