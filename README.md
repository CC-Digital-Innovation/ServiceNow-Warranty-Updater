# ServiceNow Warranty Updater

## Summary
Updates warranty and end-of-life information for Cisco and Meraki records as 
well as warranty information for Dell records inside a ServiceNow CMDB.

_Note: If you have any questions or comments you can always use GitHub
discussions, or email me at farinaanthony96@gmail.com._

#### Why
Keeps our ServiceNow CMDB updated with the latest device warranties and 
end-of-life information that enables project managers to notify clients when
they need to start considering a new warranty or replacing their old devices.

## Requirements

#### Language
- Python 3.11.1

#### Python Libraries
- oauthlib
- pysnow
- python-dotenv
- python-magic-bin (if running on a Windows OS)
- pytz
- requests_oauthlib

#### API Access
- Cisco Support API (Warranty and EOX)
- Dell TechDirect API

## Usage
- Environment variables must be set up with relevant ServiceNow, Cisco, and 
  Dell API information as well as the name of the logger for the script. 
  Reference the ".env.example" file to see which environment variables are 
  required.

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
-  version 2.1.0 - 2025/05/08
    - Replace naitive Python logger with loguru
    - Remove logging to file and PaperTrail
    - Clean up docstrings / comments
    - Update LICENSE


-  version 2.0.13 - 2023/07/19
    - Fixed logging crash
    - Changed record vs. device terminology
    - Added python-magic python in requirements.txt


-  version 2.0.1-2.0.12 - 2023/05/25
    - Several tweaks to cronjob yaml for compatibility
    - Fix logging manufacturer object instead of the string


-  version 2.0.0 - 2023/05/17
    - Refactor to make script shorter
    - Switch from config file to environment variables


-  version 1.0.0 - 2023/01/09
    - (initial release)

## Credits
Anthony Farina <<farinaanthony96@gmail.com>>
