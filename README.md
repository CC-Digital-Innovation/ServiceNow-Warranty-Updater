# ServiceNow Warranty Updater

## Summary
Updates warranty and end-of-life information for Cisco, Meraki, and Dell device
records inside a ServiceNow CMDB.

_Note: If you have any questions or comments you can always use GitHub
discussions, or email me at farinaanthony96@gmail.com._

#### Why
Keeps our ServiceNow CMDB updated with the latest device warranties and 
end-of-life information that enables project managers to notify clients when
they need to start considering a new warranty or replacing their old devices.

## Requirements
- Python 3.11.1
- oauthlib
- pysnow
- pytz
- requests

## Usage
- Edit the config file with relevant ServiceNow, Cisco, and Dell API information
  as well as the name of the global logger for the script.

- Simply run the script using Python:
  `python ServiceNow-Warranty-Updater.py`

## Compatibility
Should be able to run on any machine with a Python interpreter. This script
was only tested on a Windows machine running Python 3.11.1.

## Disclaimer
The code provided in this project is an open source example and should not
be treated as an officially supported product. Use at your own risk. If you
encounter any problems, please log an
[issue](https://github.com/CC-Digital-Innovation/ServiceNow-Warranty-Updater
/issues).

## Contributing
1. Fork it!
2. Create your feature branch: `git checkout -b my-new-feature`
3. Commit your changes: `git commit -am 'Add some feature'`
4. Push to the branch: `git push origin my-new-feature`
5. Submit a pull request ツ

## History
-  version 1.0.0 - 2023/01/09
    - (initial release)

## Credits
Anthony Farina <<farinaanthony96@gmail.com>>
