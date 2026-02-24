import json
import os
import textwrap

def cloudwatch_agent_start(cloudwatch_config, table_name,userId,processVersionId):
    return textwrap.dedent(f"""export UUID_STREAM=$(uuidgen -r)
cat > /opt/aws/amazon-cloudwatch-agent/bin/config.json << EOF
{json.dumps(cloudwatch_config, indent=4)}
EOF
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json
{update_log_robot_table(table_name, userId, processVersionId)}""")

def cloudwatch_agent_init() :
    return textwrap.dedent(f'''cd /tmp
wget https://s3.ap-southeast-1.amazonaws.com/amazoncloudwatch-agent-ap-southeast-1/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
rpm -U ./amazon-cloudwatch-agent.rpm''')

def instance_init(robot_bucket, robot_uri, cloudwatch_agent_start):
    return textwrap.dedent(f'''#!/bin/bash
echo '#!/bin/bash
cd /home/ec2-user/robot \\
&& source ~/.bash_profile \\
&& source /etc/profile.d/conda.sh \\
&& {cloudwatch_agent_start} \\
&& conda deactivate\\
&& conda activate robotenv \\
&& conda install -y boto3 packaging \\
&& sudo aws s3 cp s3://{robot_bucket}/utils/setup.sh ./ \\
&& sudo aws s3 cp s3://{robot_bucket}/utils/upload_run.py ./ \\
&& dos2unix ./setup.sh \\
&& export ROBOT_FILE={robot_uri}/robot-code.json \\
&& sudo chmod -R 777 /home/ec2-user/robot \\
&& bash setup.sh >> /var/log/robot.log \\
&& sudo aws s3 cp /var/log/robot.log s3://{robot_bucket}/{robot_uri}/run/ \\
&& sudo aws s3 cp ./report.html s3://{robot_bucket}/{robot_uri}/run/ \\
&& sudo aws s3 cp ./log.html s3://{robot_bucket}/{robot_uri}/run/ \\
' > /var/lib/cloud/scripts/per-boot/script.sh 
''')
    
def update_log_robot_table(table_name, userId, processIdVersion):
    dynamoDB_CMD = f"""aws dynamodb update-item \\
--table-name {table_name} \\
--region ap-southeast-1 \\
--key "{{\\"userId\\": {{\\"S\\": \\"{userId}\\"}}, \\"processIdVersion\\": {{\\"S\\": \\"{processIdVersion}\\"}}}}" \\
--update-expression "SET logGroup = :lg, logStream = :ls" \\
--expression-attribute-values "{{\\":lg\\": {{\\"S\\": \\""$log_group"\\"}}, \\":ls\\": {{\\"S\\": \\""$log_stream"\\"}}}}" """
 
    return textwrap.dedent(f"""log_group=$(jq -r '.logs.logs_collected.files.collect_list[0].log_group_name' /opt/aws/amazon-cloudwatch-agent/bin/config.json)
log_stream=$(jq -r '.logs.logs_collected.files.collect_list[0].log_stream_name' /opt/aws/amazon-cloudwatch-agent/bin/config.json)
{dynamoDB_CMD}""")

def create_env_variable(userId, processId, processVersion):
    return textwrap.dedent(f'''# Add environment variable setup to shell configuration file
echo "export MAIN_SERVER_API=\"{os.environ.get('MAIN_SERVER_API')}\"" | sudo tee -a ~/.bash_profile
echo "export SERVICE_KEY=\"{os.environ.get('SERVICE_KEY')}\"" | sudo tee -a ~/.bash_profile
echo "export USER_ID=\"{userId}\"" | sudo tee -a ~/.bash_profile
echo "export PROCESS_ID=\"{processId}\"" | sudo tee -a ~/.bash_profile
echo "export PROCESS_VERSION=\"{processVersion}\"" | sudo tee -a ~/.bash_profile
echo "export ROBOT_FOLDER=\"/home/ec2-user/robot\"" | sudo tee -a ~/.bash_profile
echo "export ROBOT_CREDENTIAL_FOLDER=\"\$ROBOT_FOLDER/devdata\"" | sudo tee -a ~/.bash_profile

source ~/.bash_profile
''')