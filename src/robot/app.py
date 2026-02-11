import json
import botocore
from utils.utils import *

def run_robot(event, context):
    print(f'Event: {json_prettier(event)}')
    robot_table = get_robot_table()

    if type(event['body']) == str:
        body  = json.loads(event['body'])
    else:
        body = event['body']

    user_id = body['user_id']
    process_id = body['process_id']
    version = body['version']
    trigger_type = body['trigger_type']
    # # is_simulate = body.get('is_simulate', False)
    # if is_simulate:
    #     return run_robot_simulate(event, body)
    try:
        robot_response = robot_table.get_item(Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'})
    except botocore.exceptions.ClientError as err: 
        robot_response = None

    if robot_response and "Item" in robot_response:
        instance_id = robot_response["Item"]["instanceId"]
        instance_state = robot_response["Item"]["instanceState"]
        if instance_state == "stopped":
            return handle_start_robot_instance(user_id, process_id, version, instance_id, trigger_type)
        elif instance_state == "running":
            return error_response(400, "Robot Instance Already Running", "Cannot Start Running Instance")
        else:
            return error_response(400, "Robot Instance Not Stable", "Wait for a while and try again.")
    else:
        return handle_launch_instance(user_id, process_id, version, trigger_type, False)


# def run_robot_simulate(event, body):
#     """
#     Lambda function for simulation mode.
#     - If instance stopped -> start instance
#     - If instance running -> use SSM Run Command to trigger robot execution
#     - If instance executing and force_restart=true -> kill current process and restart
#     - EC2 will NOT shutdown after robot execution, auto-shutdown after 10min idle
    
#     Precondition: Instance must be launched before (use run_robot first time)
#     """
#     print(f'Event (Simulate): {json_prettier(event)}')
#     robot_table = get_robot_table()
#     user_id = body['user_id']
#     process_id = body['process_id']
#     version = body['version']
#     run_type = body.get('run_type', 'step-by-step')  # Default to step-by-step for simulation
#     force_restart = body.get('force_restart', False)  # Allow force restart when executing
#     trigger_type = body['trigger_type']
#     try:
#         robot_response = robot_table.get_item(Key={"userId": user_id, "processIdVersion": f'{process_id}.{version}'})
#     except botocore.exceptions.ClientError as err:
#         robot_response = None

#     if robot_response and "Item" in robot_response:
#         instance_id = robot_response["Item"]["instanceId"]
#         instance_state = robot_response["Item"]["instanceState"]
        
#         if instance_state == "stopped":
#             # Start instance and run robot in simulate mode
#             return handle_simulate_robot_instance(user_id, process_id, version, instance_id, run_type, is_running=False)
#         elif instance_state == "running":
#             # Instance already running, use SSM to trigger robot execution
#             return handle_simulate_robot_instance(user_id, process_id, version, instance_id, run_type, is_running=True)
#         elif instance_state == "executing":
#             if force_restart:
#                 # Kill current robot process and restart
#                 return handle_simulate_robot_instance(user_id, process_id, version, instance_id, run_type, is_running=True, force_restart=True)
#             else:
#                 return error_response(400, "Robot Already Executing", "Robot is currently executing. Set force_restart=true to kill and restart.")
#         else:
#             return error_response(400, "Robot Instance Not Stable", "Wait for a while and try again.")
#     else:
#         return handle_launch_instance(user_id, process_id, version, trigger_type)


def stop_robot(event, context):
    print(f'Event: {json_prettier(event)}')
    robot_table = get_robot_table()

    body  = json.loads(event['body'])

    user_id = body['user_id']
    process_id = body['process_id']
    version = body['version']

    robot_response = robot_table.get_item(Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'})

    if "Item" in robot_response:
        instance_id = robot_response["Item"]["instanceId"]
        instance_state = robot_response["Item"]["instanceState"]
        if instance_state == "running":
            return handle_stop_robot_instance(user_id, process_id, version, instance_id)
        elif instance_state == "stopped":
            return error_response(400, "Robot Instance Already Stopped", "Cannot Stop Stopped Instance")
        else:
            return error_response(400, "Robot Instance Not Stable", "Wait for a while and try again.")
    else:
        return error_response(400, "Robot Instance Not Found", "Cannot Stop Non-Existent Instance")


def get_robot_detail(event, context):
    print(f'Event: {json_prettier(event)}')
    robot_table = get_robot_table()
    print(f'\nRobot Table: {robot_table}')
    query  = event['queryStringParameters']
    user_id = query['user_id']
    process_id = query['process_id']
    version = query['version']

    try:
        robot_response = robot_table.get_item(Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'})
        print(f'Robot Response of robot {user_id}.{process_id}.{version}: {robot_response}')
        if "Item" in robot_response:
            return success_response(robot_response["Item"])
        else:
            return success_response({"instanceState": "not running"})
    except Exception as e:
        return error_response(400, "Cannot Get Robot Detail", str(e))
    
def update_robot_state(event, context):
    print(f'Event: {json_prettier(event)}')
    robot_table = get_robot_table()

    instance_id = event["detail"]["instance-id"]
    instance_state = event["detail"]["state"]
    instance_name = get_instance_name(instance_id)

    if instance_name == None or instance_name.split(".")[0] != "edu-rpa-robot":
        return success_response({})
    [user_id, process_id, version] = instance_name.split(".")[1:]

    try:
        if instance_state != "terminated":
            print(f"Instance {instance_id} is {instance_state}")
            robot_table.update_item(
                Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'},
                UpdateExpression = "set instanceState=:s",
                ExpressionAttributeValues = {":s": instance_state}
            )
        else:
            robot_table.delete_item(Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'})
    except Exception as e:
        return error_response(400, "Cannot Update Robot Detail", str(e))
    
def terminate_robot_instance(event, context):
    print(f'Event: {json_prettier(event)}')
    robot_table = get_robot_table()

    body  = json.loads(event['body'])
    user_id = body['user_id']
    process_id = body['process_id']
    version = body['version']

    robot_response = robot_table.get_item(Key = {"userId": user_id, "processIdVersion": f'{process_id}.{version}'})
    if "Item" in robot_response:
        instance_id = robot_response["Item"]["instanceId"]
        try:
            ec2 = boto3.client("ec2")
            ec2.terminate_instances(InstanceIds=[instance_id])
            return success_response({})
        except Exception as e:
            return error_response(400, "Cannot Terminate Robot Instance", str(e))
    else:
        return success_response({})