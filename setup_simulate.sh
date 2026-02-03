#!/bin/bash

# setup_simulate.sh - Setup script for simulation mode
# This script does NOT shutdown after robot execution
# Auto-shutdown after 10 minutes of inactivity

bucket_name="rpa-robot-bktest"
object_name="$ROBOT_FILE"
IDLE_TIMEOUT=600  # 10 minutes in seconds

# Dependency map (same as setup.sh)
declare -A dependency_map=(
    ["RPA.Cloud.Google"]="rpaframework-google"
    ["RPA.Cloud.AWS"]="rpaframework-aws"
    ["EduRPA.Document"]="edurpa-document"
    ["EduRPA.Google"]="edurpa-cloud"
    ["EduRPA.Storage"]="edurpa-cloud"
    ["pytorch"]="pytorch torchvision cpuonly -c pytorch"
    ["PDF"]="rpaframework-pdf"
    ["RPA.MOCK_SAP"]="rpa-sap-mock-bk"
    ["RPA.Moodle"]="rpa-moodle"
    ["RPA.ERPNext"]="rpa-erpnext"
)

install_dependencies_from_robot_file() {
    local robot_code=$1
    local dependencies=("robotframework" "rpaframework" "importlib-metadata>=6.0.0")
    
    imports=$(jq -r '.resource.imports[].name' <<< "$robot_code")
    
    for lib in $imports; do
        echo "Lib: $lib"
        if [[ -n ${dependency_map[$lib]} ]]; then
            dependencies+=(${dependency_map[$lib]})
        else
            parent_module=$(cut -d'.' -f1 <<< "$lib")
            if [[ -n ${dependency_map[$parent_module]} ]]; then
                dependencies+=(${dependency_map[$parent_module]})
            fi
        fi
    done
    
    dependencies=($(echo "${dependencies[@]}" | tr ' ' '\n' | awk '!seen[$0]++' | tr '\n' ' '))

    is_edurpa_document=false

    for dependency in "${dependencies[@]}"; do
        package_not_installed=($(check_package_installed "$dependency"))
        if [[ ${#package_not_installed[@]} -eq 0 ]]; then
            continue
        fi
        echo "Packages Not Installed: ${package_not_installed[*]}"

        install_command=("pip" "install" $dependency)
        echo "${install_command[@]}"
        "${install_command[@]}"

        if [[ $dependency == *"edurpa-document"* ]]; then
            is_edurpa_document=true
            install_command=("conda" "install" "-y" ${dependency_map["pytorch"]})
            echo "${install_command[@]}"
            "${install_command[@]}"
        fi

        echo "${install_command[@]}"
        "${install_command[@]}"
    done

    if $is_edurpa_document; then
        install_command=("pip" "install" "Pillow==9.5.0")
        echo "Running: ${install_command[@]}"
        "${install_command[@]}"
    fi
}

download_json_from_s3() {
    local bucket_name=$1
    local object_name=$2
    
    echo "====== Downloading robot code ======"
    echo "Bucket: s3://$bucket_name/$object_name"
    
    aws s3 cp s3://$bucket_name/$object_name ./robot.json
    local download_exit_code=$?
    
    if [ $download_exit_code -ne 0 ]; then
        echo "ERROR: Failed to download robot.json from S3"
        return 1
    fi
    
    if [ ! -f "./robot.json" ] || [ ! -s "./robot.json" ]; then
        echo "ERROR: robot.json file not found or empty"
        return 1
    fi
    
    echo "✓ robot.json downloaded successfully"
    return 0
}

is_package_installed() {
    local package_name=$1
    [[ $(pip show "$package_name" 2>/dev/null) ]] && return 0 || return 1
}

check_package_installed() {
    local command=("$@")
    local package_not_installed=()

    for package in "${command[@]}"; do
        if [[ $package != -* && $package != http* ]]; then
            if [[ $package == *"=="* ]]; then
                package_name=$(cut -d'=' -f1 <<< "$package")
            else
                package_name=$package
            fi
            if ! is_package_installed "$package_name"; then
                package_not_installed+=("$package_name")
            fi
        fi
    done

    echo "${package_not_installed[@]}"
}

function update_instance_state() {
    local table_name="robot"
    local user_id="$USER_ID"
    local process_id_version="$PROCESS_ID.$PROCESS_VERSION"
    local new_instance_state="$1"

    echo "Robot State: " $new_instance_state

    if [ -z "$table_name" ] || [ -z "$user_id" ] || [ -z "$process_id_version" ] || [ -z "$new_instance_state" ]; then
        echo "Usage: update_instance_state <new_instance_state>"
        return 1
    fi

    aws dynamodb update-item \
        --table-name "$table_name" \
        --region ap-southeast-1 \
        --key '{ "userId": { "S": "'"$user_id"'" }, "processIdVersion": { "S": "'"$process_id_version"'" } }' \
        --update-expression "SET instanceState = :state" \
        --expression-attribute-values '{ ":state": { "S": "'"$new_instance_state"'" } }' \
        --return-values ALL_NEW
}

fetch_run_type() {
    echo "====== Fetching Run Type from DynamoDB ======"
    local user_id="$USER_ID"
    local process_id_version="$PROCESS_ID.$PROCESS_VERSION"
    
    RUN_TYPE=$(aws dynamodb get-item \
        --table-name robot \
        --region ap-southeast-1 \
        --key '{"userId": {"S": "'"$user_id"'"}, "processIdVersion": {"S": "'"$process_id_version"'"}}' \
        --projection-expression "runType" \
        --output json | jq -r '.Item.runType.S // "step-by-step"')
    
    echo "Fetched RUN_TYPE: $RUN_TYPE"
    
    if [ "$RUN_TYPE" == "step-by-step" ]; then
        export STEP_MODE="step"
    else
        export STEP_MODE="all"
    fi
    
    echo "Set STEP_MODE=$STEP_MODE"
}

check_simulate_mode() {
    echo "====== Checking Simulate Mode ======"
    local user_id="$USER_ID"
    local process_id_version="$PROCESS_ID.$PROCESS_VERSION"
    
    SIMULATE_MODE=$(aws dynamodb get-item \
        --table-name robot \
        --region ap-southeast-1 \
        --key '{"userId": {"S": "'"$user_id"'"}, "processIdVersion": {"S": "'"$process_id_version"'"}}' \
        --projection-expression "simulateMode" \
        --output json | jq -r '.Item.simulateMode.BOOL // false')
    
    echo "Simulate Mode: $SIMULATE_MODE"
}

run_robot() {
    update_instance_state executing
    echo "====== Running Robot ======"
    
    fetch_run_type
    
    # Record activity timestamp
    echo $(date +%s) > /tmp/last_robot_activity
    
    export PYTHONPATH=$PYTHONPATH:$(pwd)/src
    python3 -m robot --listener robot.utils.probe_listener.ProbeListener --output NONE --log NONE --report NONE robot.json >> /var/log/robot.log 2>&1
    
    robot_exit_code=$?
    echo "Robot execution completed with exit code: $robot_exit_code"
    
    # Update activity timestamp after execution
    echo $(date +%s) > /tmp/last_robot_activity
    
    # Update state to running (ready for next simulation)
    update_instance_state running
    
    return $robot_exit_code
}

idle_monitor() {
    # Background process to monitor idle time and shutdown if inactive
    echo "====== Starting Idle Monitor (timeout: ${IDLE_TIMEOUT}s) ======"
    
    while true; do
        sleep 60  # Check every minute
        
        if [ -f /tmp/last_robot_activity ]; then
            last_activity=$(cat /tmp/last_robot_activity)
            current_time=$(date +%s)
            idle_time=$((current_time - last_activity))
            
            echo "[Idle Monitor] Idle time: ${idle_time}s / ${IDLE_TIMEOUT}s"
            
            if [ $idle_time -ge $IDLE_TIMEOUT ]; then
                echo "[Idle Monitor] Idle timeout reached. Shutting down..."
                update_instance_state stopped
                sudo shutdown now
                exit 0
            fi
        fi
    done
}

main_simulate() {
    update_instance_state setup
    
    # Download robot code from S3
    if ! download_json_from_s3 "$bucket_name" "$object_name"; then
        echo "FATAL: Cannot proceed without robot code"
        update_instance_state failed
        sudo shutdown now
        exit 1
    fi
    
    robot_code=$(<robot.json)

    echo "====== Installing Dependencies ======"
    install_dependencies_from_robot_file "$robot_code"

    echo "====== Get Robot Credentials ======"
    get-credential

    # Initialize activity timestamp
    echo $(date +%s) > /tmp/last_robot_activity
    
    # Check if simulate mode
    check_simulate_mode
    
    if [ "$SIMULATE_MODE" == "true" ]; then
        echo "====== SIMULATE MODE: EC2 will stay running ======"
        
        # Start idle monitor in background
        idle_monitor &
        IDLE_MONITOR_PID=$!
        echo "Idle monitor started with PID: $IDLE_MONITOR_PID"
        
        # Run robot for first time
        run_robot
        
        echo "====== Waiting for next simulation request ======"
        echo "EC2 will shutdown after ${IDLE_TIMEOUT}s of inactivity"
        
        # Keep the script running (idle monitor will handle shutdown)
        wait $IDLE_MONITOR_PID
    else
        # Normal mode - run once and shutdown
        run_robot
        
        echo "====== Turning off Robot ======"
        sudo shutdown now
    fi
}

main_simulate
