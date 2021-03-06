
import bluetooth
import keyboard
from inputs import devices
import json
import boto3
import csv


def connect_to_database():
    # Variables for connecting to the database for streaming commands from the web page
    # TODO make this robust it just connects once when you launch hub.py
    table_name = 'Todo-tkqiyw7abzbm3iilppwpgk3grm-main'
    credentials_csv_file_name = 'dynamodb-stream-readonly-creds.csv'

    with open(credentials_csv_file_name, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        credentials = next(reader)

    # Connect to AWS DynamoDB API
    client = boto3.client(
        service_name='dynamodbstreams',
        region_name='us-east-2',
        aws_access_key_id=credentials['Access key ID'],
        aws_secret_access_key=credentials['Secret access key'])

    # Find the correct stream ARN
    print('Attempting to connect to table: ', table_name)
    response = client.list_streams(
        TableName=table_name
    )
    stream_arn = None
    for stream in response['Streams']:
        if stream['TableName'] == table_name:
            stream_arn = stream['StreamArn']
            break
    if stream_arn is None:
        raise ValueError('Table name {} not found in current account or no streams available'.format(table_name))
    print('Found stream: ', stream_arn)

    # Get the latest shard
    response = client.describe_stream(
        StreamArn=stream_arn
    )
    shard_id = response['StreamDescription']['Shards'][-1]['ShardId']

    # Get the first shard iterator of the latest shard
    response = client.get_shard_iterator(
        StreamArn=stream_arn,
        ShardId=shard_id,
        ShardIteratorType='LATEST',
    )
    # pp.pprint(response['ShardIterator'])
    shard_iterator = response['ShardIterator']
    print('Shard iterator: ', shard_iterator)

    return client, shard_iterator


def connect_to_bluetooth(target, port=None):
    # Check input class
    if target.__class__ is not str:
        raise TypeError('Target must be string of device name or MAC address')

    # Check if the target fits the format of a MAC address
    target_address = None
    check = target.split(':')
    if len(check) == 6 and all(len(key) == 2 for key in check):
        print('MAC address detected')
        target_address = target

    # Connect to our bluetooth module if we found it
    # Search for bluetooth device if address is not provided
    if target_address is None:
        # Scan for nearby devices
        print('Scanning for nearby devices...')
        nearby_devices = bluetooth.discover_devices()

        # Try and find our bluetooth module
        print('Searching for our bluetooth module...')
        for bluetooth_device_address in nearby_devices:
            print(bluetooth.lookup_name(bluetooth_device_address))
            if target == bluetooth.lookup_name(bluetooth_device_address):
                target_address = bluetooth_device_address
                break

        # Check we found our target
        if target_address is None:
            print('Failed to find target bluetooth device nearby')
            return None

        print('Found target bluetooth device with address', target_address)

    print('Attempting to connect')
    sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    # TODO make this not need to search over range of ports
    # TODO make this set the device address
    # Loop over ports until we find one that works (kinda hacky but it works)
    # Only check the port provided if it is given
    max_port = port if port is not None else 3
    port = port if port is not None else 0
    for port in range(port, max_port + 1):
        print('Checking Port', port)
        try:
            sock.connect((target_address, port))
            break
        except OSError as e:
            # OSError when failing to connect, skip and go to next port unless we've run out of ports in the range
            if port == max_port:
                print('Failed to connect')
                return None

    print('Connected to {} at {}:{}'.format(target, target_address, port))
    return sock


def read_keyboard_commands():
    """
    Bit Positions
    76543210
    0: Y pressed
    1: B pressed
    2: A pressed
    3: X pressed
    4-5: Forwards/Backwards - 00-Nothing, 01-Backwards, 10-Forwards, 11-Nothing
    6-7: Left/Right - 00-Nothing, 01-Right, 10-Left, 11-Nothing
    """
    cmd_flags = 0x00
    if keyboard.is_pressed('s'):
        cmd_flags |= 1 << 4
    if keyboard.is_pressed('w'):
        cmd_flags |= 1 << 5
    if keyboard.is_pressed('a'):
        cmd_flags |= 1 << 6
    if keyboard.is_pressed('d'):
        cmd_flags |= 1 << 7

    if keyboard.is_pressed('shift'):
        cmd_flags |= 0b1111

    return cmd_flags


def read_controller_commands():
    """
    Bit Positions
    76543210
    0: Y pressed
    1: B pressed
    2: A pressed
    3: X pressed
    4-5: Forwards/Backwards - 00-Nothing, 01-Backwards, 10-Forwards, 11-Nothing
    6-7: Left/Right - 00-Nothing, 01-Right, 10-Left, 11-Nothing
    """
    cmd_flags = 0x00

    # Try to find a controller if plugged in
    events = devices.gamepads[0].read()
    for event in events:
        # Ignore sync messages, no interesting information
        if event.ev_type == 'Sync':
            continue

        # Interpret buttons
        if event.code == 'BTN_NORTH':
            cmd_flags |= event.state << 0
        elif event.code == 'BTN_EAST':
            cmd_flags |= event.state << 1
        elif event.code == 'BTN_SOUTH':
            cmd_flags |= event.state << 2
        elif event.code == 'BTN_WEST':
            cmd_flags |= event.state << 3

        # Interpret d-pad
        elif event.code == 'ABS_HAT0Y':
            event.state = 2 if event.state < 0 else event.state
            cmd_flags |= event.state << 4
        elif event.code == 'ABS_HAT0X':
            event.state = 2 if event.state < 0 else event.state
            cmd_flags |= event.state << 6

        # Interpret left joystick
        elif event.code == 'ABS_Y':
            if event.state > 4000:
                cmd_flags |= 1 << 5
            elif event.state < -4000:
                cmd_flags |= 1 << 4
        elif event.code == 'ABS_X':
            if event.state > 4000:
                cmd_flags |= 1 << 7
            elif event.state < -4000:
                cmd_flags |= 1 << 6

    return cmd_flags


def read_database_commands(cl, it, previous_command_flags):
    """
    Bit Positions
    76543210
    0: Y pressed
    1: B pressed
    2: A pressed
    3: X pressed
    4-5: Forwards/Backwards - 00-Nothing, 01-Backwards, 10-Forwards, 11-Nothing
    6-7: Left/Right - 00-Nothing, 01-Right, 10-Left, 11-Nothing
    """
    response = cl.get_records(
        ShardIterator=it
        # Limit=1
    )
    # pp.pprint(response['Records'])
    if 'NextShardIterator' in response.keys():
        it = response['NextShardIterator']

    if 'Records' in response.keys() and len(response['Records']) > 0:
        keys_pressed = json.loads(response['Records'][0]['dynamodb']['NewImage']['description']['S'])
        print(keys_pressed)
        cmd_flags = 0x00

        if keys_pressed['KeyS']:
            cmd_flags |= 1 << 4
        if keys_pressed['KeyW']:
            cmd_flags |= 1 << 5
        if keys_pressed['KeyA']:
            cmd_flags |= 1 << 6
        if keys_pressed['KeyD']:
            cmd_flags |= 1 << 7

        if keys_pressed['ShiftLeft']:
            cmd_flags |= 0b1111
    else:
        cmd_flags = previous_command_flags

    return cmd_flags, it
