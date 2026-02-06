#!/bin/bash

bucket_name="rpa-robot-bktest"
object_name="$ROBOT_FILE"

# Dependency map
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
    # Read the contents of the Robot Framework file
    local robot_code=$1
    local dependencies=("robotframework" "rpaframework" "importlib-metadata>=6.0.0")
    
    imports=$(jq -r '.resource.imports[].name' <<< "$robot_code")
    
    for lib in $imports; do
        echo "Lib: $lib"
        echo "DEBUG: Checking dependency_map[$lib] = ${dependency_map[$lib]}"
        if [[ -n ${dependency_map[$lib]} ]]; then
            echo "DEBUG: Adding ${dependency_map[$lib]} to dependencies"
            dependencies+=(${dependency_map[$lib]})
        else
            parent_module=$(cut -d'.' -f1 <<< "$lib")
            echo "DEBUG: Checking parent_module: $parent_module"
            if [[ -n ${dependency_map[$parent_module]} ]]; then
                echo "DEBUG: Adding ${dependency_map[$parent_module]} from parent module"
                dependencies+=(${dependency_map[$parent_module]})
            fi
        fi
    done
    
    echo "DEBUG: Final dependencies array: ${dependencies[@]}"
    
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

        # Run the package install command
        echo "${install_command[@]}"
        "${install_command[@]}"
    done

    if $is_edurpa_document; then
        install_command=("pip" "install" "Pillow==9.5.0")
        echo "Running: ${install_command[@]}"
        "${install_command[@]}"
    fi
}

# Download JSON file from S3
download_json_from_s3() {
    local bucket_name=$1
    local object_name=$2
    
    echo "====== Downloading robot code ======"
    echo "Bucket: s3://$bucket_name/$object_name"
    
    # Try to download the file
    aws s3 cp s3://$bucket_name/$object_name ./robot.json
    local download_exit_code=$?
    
    # Check if download was successful
    if [ $download_exit_code -ne 0 ]; then
        echo "ERROR: Failed to download robot.json from S3 (exit code: $download_exit_code)"
        echo "Please check:"
        echo "  1. S3 bucket exists: $bucket_name"
        echo "  2. Object exists: $object_name"
        echo "  3. EC2 instance has proper IAM role/permissions"
        return 1
    fi
    
    # Verify the file was actually created
    if [ ! -f "./robot.json" ]; then
        echo "ERROR: robot.json file not found after download"
        return 1
    fi
    
    # Check if file is not empty
    if [ ! -s "./robot.json" ]; then
        echo "ERROR: robot.json file is empty"
        return 1
    fi
    
    echo "✓ robot.json downloaded successfully ($(stat -f%z "./robot.json" 2>/dev/null || stat -c%s "./robot.json") bytes)"
    return 0
}

# Check if package installed  
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
                installed_version=$(cut -d'=' -f2 <<< "$package")
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

wait_for_sync() {
    file="/opt/aws/amazon-cloudwatch-agent/logs/state/_var_log_robot.log"
    previous_checksum=""

    while true; do
        current_checksum=$(md5sum "$file" | awk '{print $1}')

        if [ "$current_checksum" != "$previous_checksum" ]; then
            previous_checksum="$current_checksum"
        else
            break
        fi
        sleep 10
    done
}

function update_instance_state() {
    local table_name="robot"
    local user_id="$USER_ID"
    local process_id_version="$PROCESS_ID.$PROCESS_VERSION"
    local new_instance_state="$1"

    echo "Robot State: " $new_instance_state

    # Validate input parameters
    if [ -z "$table_name" ] || [ -z "$user_id" ] || [ -z "$process_id_version" ] || [ -z "$new_instance_state" ]; then
        echo "Usage: update_instance_state <table_name> <user_id> <process_id_version> <new_instance_state>"
        return 1
    fi

    # Update instanceState attribute using AWS CLI
    aws dynamodb update-item \
        --table-name "$table_name" \
        --region ap-southeast-1 \
        --key '{ "userId": { "S": "'"$user_id"'" }, "processIdVersion": { "S": "'"$process_id_version"'" } }' \
        --update-expression "SET instanceState = :state" \
        --expression-attribute-values '{ ":state": { "S": "'"$new_instance_state"'" } }' \
        --return-values ALL_NEW
}

main() {
    update_instance_state setup
    
    # Download robot code from S3
    if ! download_json_from_s3 "$bucket_name" "$object_name"; then
        echo "FATAL: Cannot proceed without robot code"
        update_instance_state failed
        echo "====== Turning off Robot ======"
        wait_for_sync
        sudo shutdown now
        exit 1
    fi
    
    robot_code=$(<robot.json)

    echo "====== Installing Dependencies ======"
    install_dependencies_from_robot_file "$robot_code"

    echo "====== Get Robot Credentials ======"
    get-credential

    update_instance_state executing
    echo "====== Running Robot ======"

    python3 -m robot robot.json >> /var/log/robot.log 2>&1

    robot_exit_code=$?
    
    echo "Robot execution completed with exit code: $robot_exit_code"

    update_instance_state cooldown
    echo "====== Update Robot Run Result ======"
    
    # Only upload results if output.xml exists
    if [ -f "./output.xml" ]; then
        echo "✓ output.xml found, uploading results..."
        python3 upload_run.py --output_xml_path="./output.xml" --user_id="$USER_ID" --process_id_version="$PROCESS_ID.$PROCESS_VERSION.detail" >> /var/log/robot.log 2>&1
        upload_exit_code=$?
        
        if [ $upload_exit_code -eq 0 ]; then
            echo "✓ Results uploaded successfully"
        else
            echo "⚠ Failed to upload results (exit code: $upload_exit_code)"
        fi
    else
        echo "⚠ WARNING: output.xml not found after robot execution"
        echo "Robot exit code was: $robot_exit_code"
        echo "This usually means the robot execution failed before generating output"
        echo "Check /var/log/robot.log for details"
    fi

    echo "====== Turning off Robot ======"
    wait_for_sync
    sudo shutdown now
}

main
