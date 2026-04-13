# 1. Clone repository
git clone https://github.com/your-repo/pumpfun-bot.git
cd pumpfun-bot/terraform

# 2. Create terraform.tfvars from example
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 3. Initialize Terraform
terraform init

# 4. Create workspace for environment
terraform workspace new dev
terraform workspace select dev

# 5. Plan deployment
terraform plan -var-file="environments/dev/terraform.tfvars"

# 6. Apply deployment
terraform apply -var-file="environments/dev/terraform.tfvars"

# 7. Verify deployment
terraform output
curl https://$(terraform output -raw bot_alb_dns)/health

# 8. Deploy to production
terraform workspace new prod
terraform apply -var-file="environments/prod/terraform.tfvars"