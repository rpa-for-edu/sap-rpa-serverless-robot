import boto3
from botocore.exceptions import ClientError
import json
import pymysql
from notification import *

MAP_EVENT_TO_ARN = {
    'event-gmail': 'arn:aws:lambda:ap-southeast-1:678601387840:function:edu-rpa-serverless-robot-CheckNewEmailsFunction-x00iwDPvEQXr',
    'event-drive': 'arn:aws:lambda:ap-southeast-1:678601387840:function:edu-rpa-serverless-robot-CheckNewFilesFunction-ecqRwOPcGZk5',
    'event-forms': 'arn:aws:lambda:ap-southeast-1:678601387840:function:edu-rpa-serverless-robot-CheckNewResponsesFunction-4EiwCv5GVWfP',
}

RUN_ROBOT_ARN = 'arn:aws:lambda:ap-southeast-1:678601387840:function:edu-rpa-serverless-robot-RunRobotFunction-CGVg2ON5Z83i'

def get_secret():
    secret_name = "edu-rpa/dev/secrets"
    region_name = "ap-southeast-2"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        raise e

    secret = get_secret_value_response['SecretString']
    return json.loads(secret)

def get_connection(secret):
    # RDS connection parameters
    rds_host = secret.get('MYSQL_HOST')
    username = secret.get('MYSQL_USERNAME')
    password = secret.get('MYSQL_PASSWORD')
    db_name = secret.get('MYSQL_DATABASE')

    conn = None

    try:
        # Connect to the database
        conn = pymysql.connect(
            host=rds_host, 
            user=username, 
            passwd=password, 
            db=db_name, 
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor
        )

    except pymysql.MySQLError as e:
        print("ERROR: Unexpected error: Could not connect to MySQL instance.")
        print(e)

    return conn

def get_token(db_conn, service, user_id, connection_name):
    result = None

    try:
        with db_conn.cursor() as cursor:
            sql = f"SELECT * FROM connection WHERE provider = '{service}' AND userId = {user_id} AND name = '{connection_name}'"
            cursor.execute(sql)
            result = cursor.fetchone()
    except pymysql.MySQLError as e:
        print(e)

    return result

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

def handle_create_event_schedule(user_id, process_id, version, event_schedule):
    scheduler = boto3.client('scheduler')
    schedule_name = f'edu-rpa-robot-schedule.{user_id}.{process_id}.{version}'

    create_params = {
        'Name': schedule_name,
        'FlexibleTimeWindow': {
            'Mode': 'OFF'
        },
        'Target': {
            'Arn': MAP_EVENT_TO_ARN[event_schedule['type']],
            'RoleArn': 'arn:aws:iam::678601387840:role/Event_Scheduler_Role',
            'Input': json.dumps(create_check_event_input(user_id, process_id, version, event_schedule))
        },
        'ScheduleExpression': 'rate(10 minutes)',
        'State': event_schedule['state'],
    }

    try:
        response = scheduler.create_schedule(**create_params)
        return success_response(response)
    except Exception as e:
        return error_response(400, "Cannot Create Event Schedule", str(e))
    
def handle_update_event_schedule(user_id, process_id, version, event_schedule, old_schedule):
    scheduler = boto3.client('scheduler')
    schedule_name = f'edu-rpa-robot-schedule.{user_id}.{process_id}.{version}'

    update_params = {
        'Name': schedule_name,
        'ScheduleExpression': old_schedule['ScheduleExpression'],
        'State': event_schedule['state'],
        'FlexibleTimeWindow': old_schedule['FlexibleTimeWindow'],
        'Target': {
            'Arn': old_schedule['Target']['Arn'],
            'RoleArn': old_schedule['Target']['RoleArn'],
            'Input': json.dumps(create_check_event_input(user_id, process_id, version, event_schedule))
        },
    }

    try:
        response = scheduler.update_schedule(**update_params)
        return success_response(response)
    except Exception as e:
        return error_response(400, "Cannot Update Schedule", str(e))
    
def run_robot_with_event(user_id, process_id, version, event_type, event_data):
    # TODO: pass event_data to the robot lambda function
    lambda_client = boto3.client('lambda')
    function_name = RUN_ROBOT_ARN
    payload = json.dumps({
        "body": {
            "user_id": str(user_id),
            "process_id": process_id,
            "version": version,
            "trigger_type": event_type,
        }
    })

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=payload
        )
        print(f'Invoked {function_name} with payload: {payload}')
        return success_response(response)
    except Exception as e:
        print(f'Failed to invoke {function_name} with payload: {payload}')
        notify_by_trigger(
            user_id, 
            event_type, 
            "Cannot trigger bobot", 
            f"Cannot trigger bobot of process {process_id}.v{version} triggered by {event_type}: {str(e)}"
        )
        return error_response(400, "Cannot Run Robot", str(e))
    
def create_check_event_input(user_id, process_id, version, event_schedule):
    input = {
                "user_id": user_id,
                "process_id": process_id,
                "version": version,
                "connection_name": event_schedule['connection_name'],
            }
    if event_schedule['type'] == 'event-forms':
        input['form_id'] = event_schedule['form_id']
    else:
        input['filter'] = event_schedule['filter']

    return input