import json
import os
import boto3

def json_prettier(jsonData) :
    # NOTE: disable json prettify for CloudWatch logs
    # return json.dumps(jsonData, indent=4, sort_keys=True, default=str)
    return jsonData

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

def handle_create_schedule(user_id, process_id, version, create_schedule_dto):
    scheduler = boto3.client('scheduler')
    schedule_name = f'edu-rpa-robot-schedule.{user_id}.{process_id}.{version}'

    create_params = {
        'Name': schedule_name,
        'FlexibleTimeWindow': {
            'Mode': 'OFF'
        },
        'Target': {
            'Arn': os.environ['RUN_ROBOT_FUNCTION_ARN'],
            'RoleArn': os.environ['SCHEDULER_ROLE_ARN'],
            'Input': json.dumps({
                "body": {
                    "user_id": user_id,
                    "process_id": process_id,
                    "version": version,
                    "trigger_type": "schedule"
                }
            })
        },
        'ScheduleExpression': create_schedule_dto['schedule_expression'],
        'ScheduleExpressionTimezone': 'UTC+07:00',
        'State': 'ENABLED',
    }

    if 'schedule_expression_timezone' in create_schedule_dto:
        create_params['ScheduleExpressionTimezone'] = create_schedule_dto['schedule_expression_timezone']

    if 'start_date' in create_schedule_dto:
        create_params['StartDate'] = create_schedule_dto['start_date']

    if 'end_date' in create_schedule_dto:
        create_params['EndDate'] = create_schedule_dto['end_date']

    try:
        response = scheduler.create_schedule(**create_params)
        return success_response(response)
    except Exception as e:
        return error_response(400, "Cannot Create Schedule", str(e))
    
def handle_update_schedule(user_id, process_id, version, update_schedule_dto, old_schedule):
    scheduler = boto3.client('scheduler')
    schedule_name = f'edu-rpa-robot-schedule.{user_id}.{process_id}.{version}'

    update_params = {
        'Name': schedule_name,
        'ScheduleExpression': update_schedule_dto['schedule_expression'],
        'ScheduleExpressionTimezone': 'UTC+07:00',
        'State': 'ENABLED',
        'FlexibleTimeWindow': old_schedule['FlexibleTimeWindow'],
        'Target': old_schedule['Target'],
    }

    if 'schedule_expression_timezone' in update_schedule_dto:
        update_params['ScheduleExpressionTimezone'] = update_schedule_dto['schedule_expression_timezone']

    if 'start_date' in update_schedule_dto:
        update_params['StartDate'] = update_schedule_dto['start_date']

    if 'end_date' in update_schedule_dto:
        update_params['EndDate'] = update_schedule_dto['end_date']

    try:
        response = scheduler.update_schedule(**update_params)
        return success_response(response)
    except Exception as e:
        return error_response(400, "Cannot Update Schedule", str(e))
