# ge3t_conformal_shim
Custom Conformal Shimming Calibration and Computation Tool for GE3T MRI Scanner
* Automated using ExSI. A tool made by GE for the GE
* Built for use with OpenSourceImaging Shim Drivers
* Runs on ajacent machine to scanner computer, so that you have access to latest python environment
* GUI built using PyQT6

## Setting up and using the Shim Tool

1. Navigate to the directory and install the required packages
```bash
$ cd <path/to/shimTool>
$ pip install -r requirements.txt
```
2. Fill in the empty lines in `configs.json`. These will be dependent on personal preferences and scanner setup.
    The passwords / host ids are generally common for the GE Nspire

3. Launch the tool
```bash
$ python shimTool.py
```

## Using just the ExSI Client
