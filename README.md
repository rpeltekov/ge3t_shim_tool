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
2. [for development only] Install / setup the pre-commit hooks
    ```bash
    $ pre-commit install
    ```
    Now when you commit, the pre-commit hooks will run and lint your code before you push. If you want to run the hooks manually, refer to https://pre-commit.com/#install or use `pre-commit run --all-files`
3. There is provided a `configsTemplate.json`. Copy this file with the name `configs.json`, and adjust the contents to be dependent on personal preferences and scanner setup. The passwords / host ids are generally common for the GE Nspire

4. Launch the tool. Use `--no-gui` to launch the tool in a python CLI or use `--quiet` to silence most of the logging and output.
```bash
$ python -i src/main.py [--no-gui] [--quiet]
```

## Using just the ExSI Client
more details to be added...
