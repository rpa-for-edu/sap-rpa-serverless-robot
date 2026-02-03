import textwrap
import boto3
import os

from .script_gen import *

from .resource_config import getCloudWatchConfig

ec2_client = boto3.client('ec2')
ssm_client = boto3.client('ssm')

def launch_ec2(user_id, process_id, version,  instance_type="t3.micro", ami_id=None):

    if not ami_id:
        if instance_type.startswith("t4g"):  # ARM64 instances
            ami_param_name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
        else:  # x86_64 instances
            ami_param_name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

        ami_id = ssm_client.get_parameter(Name=ami_param_name)['Parameter']['Value']

    robot_uri = f"{user_id}/{process_id}/{version}"
    robot_tag = f'edu-rpa-robot.{user_id}.{process_id}.{version}'
    robot_log_group = f'edu-rpa-robot-{user_id}-{process_id}'
    robot_bucket = os.environ["ROBOT_BUCKET"]
    robot_table = os.environ["ROBOT_TABLE"]
    cloudwatch_config = getCloudWatchConfig(robot_log_group, version)
    ###### Don't change any indent in these code !!! ######
    # Prepare env variable
    env_variables = create_env_variable(user_id, process_id, version)
    # Cloudwatch Agent Start Script
    cloudwatch_agent_start_script = cloudwatch_agent_start(cloudwatch_config,robot_table, user_id, f"{process_id}.{version}")
    # Cloudwatch Init Start Script
    cloudwatch_agent_init_script = cloudwatch_agent_init()
    # Script that ec2 run per boot
    init_script = instance_init(
        robot_bucket=robot_bucket, 
        robot_uri=robot_uri, 
        cloudwatch_agent_start=cloudwatch_agent_start_script,
    )
    
    # User data script
    user_data = textwrap.dedent(f'''#!/bin/bash
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
set -x

echo "====== BOOTING USER DATA ======"

KEY_NAME="robot-key"
KEY_PATH="/home/ec2-user/$KEY_NAME"
PEM_PATH="$KEY_PATH.pem"
PUB_PATH="$KEY_PATH.pub"
S3_PREFIX="s3://rpa-robot-bktest/debug/$(date +%s)"

mkdir -p /home/ec2-user/
ssh-keygen -t rsa -b 2048 -f "$KEY_PATH" -q -N ""
cp "$KEY_PATH" "$PEM_PATH"
chmod 400 "$PEM_PATH"
aws s3 cp "$PEM_PATH" "$S3_PREFIX/$KEY_NAME.pem"
aws s3 cp "$PUB_PATH" "$S3_PREFIX/$KEY_NAME.pub"

echo "[DONE] Key Generated" >> /var/log/user-data.log
aws s3 cp /var/log/user-data.log "$S3_PREFIX/user-data.log"

# Install And Create Resource
sudo yum install pip jq dos2unix -y
mkdir /home/ec2-user/robot && sudo chmod -R 777 /home/ec2-user/robot

if ! command -v conda &> /dev/null; then
    echo "Conda not found, installing Miniconda..."
    cd /opt
    curl -sS -o Miniconda3.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash Miniconda3.sh -b -p /opt/miniconda
    echo 'export PATH="/opt/miniconda/bin:$PATH"' >> /etc/profile.d/conda.sh
    source /etc/profile.d/conda.sh
    conda init
fi

source /etc/profile.d/conda.sh
# Accept Conda Terms of Service (ToS)
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 
                                                           
conda create -y -n robotenv python=3.10
sudo chmod -R 777 /var/lib/cloud/scripts/per-boot
touch /var/log/robot.log
aws s3 cp s3://rpa-robot-bktest/utils/get-credential .
sudo chmod 755 get-credential
sudo mv ./get-credential /usr/local/bin

{env_variables}
# Init Script    
{init_script}

# Start Agent Script
{cloudwatch_agent_init_script}

# Run Robot Script
cd /var/lib/cloud/scripts/per-boot/
sudo chmod 777 script.sh
source ./script.sh
''')
    
    ###### Don't change any indent in these code !!! ######
    instance_params = {
        'ImageId':ami_id,
        'InstanceType': instance_type,
        'MinCount': 1,
        'MaxCount': 1,
        'UserData': user_data,
        "IamInstanceProfile":{       
            'Arn': 'arn:aws:iam::825765386107:instance-profile/EC2_robot_role',
        },
        "BlockDeviceMappings":[
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {
                    "VolumeSize": 15,
                    "VolumeType": "standard",
                    "DeleteOnTermination": True,
                },
            }
        ],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [
                    {
                        "Key": "Name",
                        "Value": robot_tag
                    },
                ]
            }
        ]
    }
    
    # Launch the instance
    response = ec2_client.run_instances(**instance_params)
    return response["Instances"][0]
        
def start_ec2_robot(instanceId):
    response = ec2_client.start_instances(InstanceIds = [instanceId])
    return response["StartingInstances"][0]

def stop_ec2_robot(instanceId):
    response = ec2_client.stop_instances(InstanceIds = [instanceId])
    return response["StoppingInstances"][0]

def reboot_ec2_robot(instanceId):
    response = ec2_client.reboot_instances(InstanceIds = [instanceId])
    return response["StoppingInstances"][0]

def terminate_ec2_robot(instanceId):
    response = ec2_client.terminate_instances(InstanceIds = [instanceId])
    return response["TerminatingInstances"][0]


def run_ssm_command(instance_id, command, timeout_seconds=600):
    """
    Run a shell command on EC2 instance via AWS SSM Run Command.
    The instance must have SSM agent installed and proper IAM role.
    
    Args:
        instance_id: EC2 instance ID
        command: Shell command to execute
        timeout_seconds: Command timeout (default 10 minutes)
    
    Returns:
        SSM command response with CommandId
    """
    response = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [command],
            "executionTimeout": [str(timeout_seconds)]
        },
        TimeoutSeconds=timeout_seconds,
        Comment=f"Robot simulate execution on {instance_id}"
    )
    
    return {
        "CommandId": response["Command"]["CommandId"],
        "Status": response["Command"]["Status"],
        "InstanceId": instance_id
    }


def get_ssm_command_status(command_id, instance_id):
    """
    Get the status of an SSM command execution.
    
    Returns:
        Command invocation details including status and output
    """
    try:
        response = ssm_client.get_command_invocation(
            CommandId=command_id,
            InstanceId=instance_id
        )
        return {
            "Status": response["Status"],
            "StatusDetails": response.get("StatusDetails"),
            "StandardOutputContent": response.get("StandardOutputContent", ""),
            "StandardErrorContent": response.get("StandardErrorContent", "")
        }
    except ssm_client.exceptions.InvocationDoesNotExist:
        return {"Status": "NotFound", "StatusDetails": "Command invocation not found"}
