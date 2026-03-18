import json
import boto3
from .utils_ec2 import *
from boto3.dynamodb.types import TypeDeserializer
from notification import *

def ddb_deserialize(r, type_deserializer = TypeDeserializer()):
    return type_deserializer.deserialize({"M": r})

def json_prettier(jsonData) :
    # NOTE: disable json prettify for CloudWatch logs
    # return json.dumps(jsonData, indent=4, sort_keys=True, default=str)
    return jsonData

def get_robot_table():
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table('robot')
    return table

def get_dynamoDB_client():
    return boto3.client('dynamodb')

def get_S3_client():
    s3 = boto3.client("s3")
    return s3

def error_response(statusCode, error, message):
    return {
        'statusCode': statusCode,
        'body': json.dumps({
            'error': error,
            'message': message,
        }, default=str)
    }

def success_response(body):
    return {
        'statusCode': 200,
        'body': json.dumps(body, default=str)
    }

def handle_launch_instance(user_id, process_id, version, trigger_type, simulateMode=False):
    robot_table = get_robot_table()

    try:
        instance_response = launch_ec2(user_id, process_id, version)
    except Exception as e:
        notify_by_trigger(
            user_id, 
            trigger_type, 
            "Cannot trigger robot", 
            f"Cannot trigger robot of process {process_id}.v{version} triggered by {trigger_type}: {str(e)}"
        )
        return error_response(400, "Cannot Launch Robot Instance", str(e))

    instance_id = instance_response["InstanceId"]
    launch_time = instance_response["LaunchTime"]
    state = instance_response["State"]["Name"]
    process_id_version = f'{process_id}.{version}'
    robot_detail = {
        "userId": user_id,
        "processIdVersion": process_id_version,
        "launchTime": str(launch_time),
        "instanceId": instance_id,
        "instanceState": state,
        "simulateMode": simulateMode,  # Normal mode - will shutdown after execution
    }

    try:
        robot_table.put_item(Item = robot_detail)
    except Exception as e:
        notify_by_trigger(
            user_id, 
            trigger_type,  
            "Cannot save robot detail", 
            f"Cannot save robot detail of process {process_id}.v{version} triggered by {trigger_type}: {str(e)}"
        )
        return error_response(400, "Cannot Update Robot Detail", str(e))
    
    notify_by_trigger(
        user_id,
        trigger_type,
        "Successfully triggered robot",
        f"Robot instance of process {process_id}.v{version} triggered by {trigger_type} launched successfully."
    )
    return success_response(robot_detail)

def handle_start_robot_instance(user_id, process_id, version, instance_id, trigger_type):
    # robot_table = get_robot_table()
    
    # # Reset simulateMode to false for normal run (will shutdown after execution)
    # try:
    #     process_id_version = f'{process_id}.{version}'
    #     robot_table.update_item(
    #         Key={"userId": user_id, "processIdVersion": process_id_version},
    #         UpdateExpression="SET simulateMode = :sm",
    #         ExpressionAttributeValues={":sm": False}
    #     )
    # except Exception as e:
    #     return error_response(400, "Cannot Update Simulate Mode", str(e))
    
    try:
        instance_response = start_ec2_robot(instance_id)
    except Exception as e:
        notify_by_trigger(
            user_id, 
            trigger_type, 
            "Cannot trigger robot", 
            f"Cannot trigger robot of process {process_id}.v{version} triggered by {trigger_type}: {str(e)}"
        )
        return error_response(400, "Cannot Start Robot Instance", str(e))
    
    current_state = instance_response["CurrentState"]["Name"]
    notify_by_trigger(
        user_id,
        trigger_type,
        "Successfully triggered robot",
        f"Robot instance of process {process_id}.v{version} triggered by {trigger_type} started successfully."
    )
    return success_response({"state": current_state})

def handle_stop_robot_instance(user_id, process_id, version, instance_id):
    try:
        instance_response = stop_ec2_robot(instance_id)
    except Exception as e:
        return error_response(400, "Cannot Stop Robot Instance", str(e))
    
    current_state = instance_response["CurrentState"]["Name"]
    return success_response({"state": current_state})

def get_instance_name(instance_id):
    ec2 = boto3.client('ec2')
    response = ec2.describe_instances(InstanceIds=[instance_id])
    return response["Reservations"][0]["Instances"][0]["Tags"][0]["Value"]


# def handle_simulate_robot_instance(user_id, process_id, version, instance_id, run_type="step-by-step", is_running=False, force_restart=False):
    """
    Handle robot simulation mode.
    - If is_running=False: Start EC2 instance 
    - If is_running=True: Use SSM Run Command to trigger robot execution without restart
    - If force_restart=True: Kill existing robot process before starting new one
    
    EC2 will NOT shutdown after execution, auto-shutdown after 10min idle.
    """
    robot_table = get_robot_table()
    process_id_version = f'{process_id}.{version}'
    
    # Update run_type and simulateMode in DynamoDB
    try:
        robot_table.update_item(
            Key={"userId": user_id, "processIdVersion": process_id_version},
            UpdateExpression="SET runType = :rt, simulateMode = :sm",
            ExpressionAttributeValues={
                ":rt": run_type,
                ":sm": True  # Mark as simulate mode
            }
        )
    except Exception as e:
        return error_response(400, "Cannot Update Simulate Settings", str(e))
    
    if not is_running:
        # Instance is stopped, start it
        try:
            instance_response = start_ec2_robot(instance_id)
            current_state = instance_response["CurrentState"]["Name"]
            return success_response({
                "state": current_state,
                "runType": run_type,
                "simulateMode": True,
                "message": "Instance starting. Robot will execute when instance is ready."
            })
        except Exception as e:
            return error_response(400, "Cannot Start Robot Instance", str(e))
    else:
        # Instance is already running, use SSM to trigger robot execution
        try:
            # DynamoDB update command to set state back to running after execution
            ddb_update_cmd = f'''
# Update DynamoDB instanceState to running after execution
aws dynamodb update-item \\
    --table-name robot \\
    --region ap-southeast-1 \\
    --key '{{"userId": {{"S": "{user_id}"}}, "processIdVersion": {{"S": "{process_id_version}"}}}}' \\
    --update-expression "SET instanceState = :state" \\
    --expression-attribute-values '{{":state": {{"S": "running"}}}}'
'''
            
            # Build command based on whether we need to force restart
            if force_restart:
                # Kill existing robot process first, then start new one
                command = f'''
cd /home/ec2-user/robot
source ~/.bash_profile
source /etc/profile.d/conda.sh
conda activate robotenv

echo "====== Force Restart: Killing existing robot process ======"
# Kill any existing robot python process
pkill -f "python3 -m robot" || true
sleep 1

# Set STEP_MODE based on run_type
export STEP_MODE="{("step" if run_type == "step-by-step" else "all")}"
echo "Running robot in simulate mode with STEP_MODE=$STEP_MODE (force_restart=true)"

# Run the robot
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python3 -m robot --listener probe_listener.ProbeListener --output NONE --log NONE --report NONE robot.json >> /var/log/robot.log 2>&1

# Reset idle timer after robot execution
echo $(date +%s) > /tmp/last_robot_activity

{ddb_update_cmd}
'''
            else:
                # Normal run without killing existing process
                command = f'''
cd /home/ec2-user/robot
source ~/.bash_profile
source /etc/profile.d/conda.sh
conda activate robotenv

# Set STEP_MODE based on run_type
export STEP_MODE="{("step" if run_type == "step-by-step" else "all")}"
echo "Running robot in simulate mode with STEP_MODE=$STEP_MODE"

# Run the robot
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
python3 -m robot --listener probe_listener.ProbeListener --output NONE --log NONE --report NONE robot.json >> /var/log/robot.log 2>&1

# Reset idle timer after robot execution
echo $(date +%s) > /tmp/last_robot_activity

{ddb_update_cmd}
'''
            ssm_response = run_ssm_command(instance_id, command)
            
            # Update instance state to executing
            robot_table.update_item(
                Key={"userId": user_id, "processIdVersion": process_id_version},
                UpdateExpression="SET instanceState = :state",
                ExpressionAttributeValues={":state": "executing"}
            )
            
            return success_response({
                "state": "executing",
                "runType": run_type,
                "simulateMode": True,
                "forceRestart": force_restart,
                "ssmCommandId": ssm_response.get("CommandId"),
                "message": f"Robot execution triggered via SSM{' (force restart)' if force_restart else ''}."
            })
        except Exception as e:
            return error_response(400, "Cannot Trigger Robot via SSM", str(e))