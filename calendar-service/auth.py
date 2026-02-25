from pyicloud import PyiCloudService
import click
import sys

def authenticate():
    api = PyiCloudService('pon.chalermpong@icloud.com', '1234S678s')

    # 2FA handling
    if api.requires_2fa:
        security_key_names = api.security_key_names

        if security_key_names:
            print(
                f"Security key confirmation is required. "
                f"Please plug in one of the following keys: {', '.join(security_key_names)}"
            )

            devices = api.fido2_devices

            print("Available FIDO2 devices:")

            for idx, dev in enumerate(devices, start=1):
                print(f"{idx}: {dev}")

            choice = click.prompt(
                "Select a FIDO2 device by number",
                type=click.IntRange(1, len(devices)),
                default=1,
            )
            selected_device = devices[choice - 1]

            print("Please confirm the action using the security key")

            api.confirm_security_key(selected_device)

        else:
            print("Two-factor authentication required.")
            code = input(
                "Enter the code you received of one of your approved devices: "
            )
            result = api.validate_2fa_code(code)
            print("Code validation result: %s" % result)

            if not result:
                print("Failed to verify security code")
                sys.exit(1)

        if not api.is_trusted_session:
            print("Session is not trusted. Requesting trust...")
            result = api.trust_session()
            print("Session trust result %s" % result)

            if not result:
                print(
                    "Failed to request trust. You will likely be prompted for confirmation again in the coming weeks"
                )

    elif api.requires_2sa:
        print("Two-step authentication required. Your trusted devices are:")

        devices = api.trusted_devices
        for i, device in enumerate(devices):
            print(
                "  %s: %s" % (i, device.get('deviceName',
                "SMS to %s" % device.get('phoneNumber')))
            )

        device = click.prompt('Which device would you like to use?', default=0)
        device = devices[device]
        if not api.send_verification_code(device):
            print("Failed to send verification code")
            sys.exit(1)

        code = click.prompt('Please enter validation code')
        if not api.validate_verification_code(device, code):
            print("Failed to verify verification code")
            sys.exit(1)

    return api