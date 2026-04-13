# State file locked
terraform force-unlock <LOCK_ID>

# Debug mode
export TF_LOG=DEBUG
export TF_LOG_PATH=./terraform.log
terraform apply

# Target specific resource
terraform apply -target=aws_instance.bot

# Remove from state without destroying
terraform state rm aws_instance.old_bot

# Move resource in state
terraform state mv aws_instance.old aws_instance.new