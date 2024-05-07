import threading, os, json
from matplotlib import pyplot as plt
import sys, paramiko, subprocess, os, threading, re
import numpy as np

from guiUtils import *

def load_config(filename):
    with open(filename, 'r') as file:
        return json.load(file)

def kickoff_thread(target, args=()):
    t = threading.Thread(target=target, args=args)
    t.daemon = True
    t.start()

def requireShimConnection(func):
    """Decorator to check if the EXSI client is connected before running a function."""
    def wrapper(self, *args, **kwargs):
        # Check the status of the event
        if not self.shimInstance.connectedEvent.is_set() and not self.debugging:
            # Show a message to the user, reconnect shim client.
            msg = createMessageBox("SHIM Client Not Connected",
                                   "The SHIM client is still not connected to shim arduino.", 
                                   "Closing Client.\nCheck that arduino is connected to the HV Computer via USB.\n" +
                                   "Check that the arduino port is set correctly using serial_finder.sh script.")
            msg.exec() 
            # have it close the exsi gui
            self.close()
            return
        return func(self)
    return wrapper

def requireExsiConnection(func):
    """Decorator to check if the EXSI client is connected before running a function."""
    def wrapper(self, *args, **kwargs):
        # Check the status of the event
        if not self.exsiInstance.connected_ready_event.is_set() and not self.debugging:
            # Show a message to the user, reconnect exsi client.
            msg = createMessageBox("EXSI Client Not Connected", 
                                   "The EXSI client is still not connected to scanner.", 
                                   "Closing Client.\nCheck that External Host on scanner computer set to 'newHV'.")
            msg.exec() 
            # have it close the exsi gui
            self.close()
            return
        return func(self)
    return wrapper

def requireAssetCalibration(func):
    """Decorator to check if the ASSET calibration scan is done before running a function."""
    def wrapper(self, *args, **kwargs):
        #TODO(rob): probably better to figure out how to look at existing scan state. somehow check all performed scans on start?
        if not self.assetCalibrationDone and not self.debugging:
            self.log("Debug: Need to do calibration scan before running scan with ASSET.")
            # Show a message to the user, reconnect exsi client.
            msg = createMessageBox("Asset Calibration Scan Not Performed",
                                   "Asset Calibration scan not detected to be completed.", 
                                   "Please perform calibration scan before continuing with this scan")
            msg.exec() 
            return
        return func(self)
    return wrapper


def saveImage(directory, title, b0map, slice_index, vmax, white=False):
    """Save B0MAP of either background, estimation, or actual to a file."""

    if b0map is None:
        return None
    name = f"{title} B0 Map Slice:{slice_index} (Hz)"
    output_path = os.path.join(directory, f"{title}_{slice_index}"+".png")

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(b0map, cmap='jet', vmin=-vmax, vmax=vmax)
    cbar = plt.colorbar(im)

    if white:
        # Set colorbar tick labels to white
        cbar.ax.yaxis.set_tick_params(color='white')
        # Set the color of the tick labels to white
        plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white')

    if white:
        plt.title(name, color='white', size=10)
    else:
        plt.title(name, size=10)
    plt.axis('off')
    
    fig.savefig(output_path, bbox_inches='tight', transparent=True)
    plt.close(fig)
    return output_path

def saveStats(directory, title, stats, volume=False):
    if stats is not None:
        if not volume:
            statlist = []
            for i in range(len(stats)):
                if stats[i] is not None:
                    stat = stats[i].replace("\n", "\n\t")
                else:
                    stat = "None"
                statlist.append(f"Index {i}:\n\t{stat}")
            outstat = "\n".join(statlist)
            output_path = os.path.join(directory, title+"_STATS.txt")
        else:
            outstat = f"Volume:\n{stats}"
            output_path = os.path.join(directory, title+"_STATS_VOLUME.txt")
        with open(output_path, 'w') as f:
            f.write(outstat)
        return output_path

def saveHistogram(directory, title, data, slice_index):
    """Save a histogram of the data of either background, estimation, actual at slice or over Full ROI to a file."""
    fig, ax = plt.subplots()
    flatdata = data.flatten()
    #ignore nans:
    flatdata = flatdata[~np.isnan(flatdata)]
    ax.hist(flatdata, bins=100, color='c', alpha=0.7, rwidth=0.85)
    # set the x-axis label
    ax.set_xlabel("Offresonance (Hz)")
    # set the title and output path
    if slice_index >= 0:
        ax.set_title(f"{title} Offresonance of Slice:{slice_index}")
        output_path = os.path.join(directory, f"{title}_{slice_index}_hist.png")
    else:
        ax.set_title(f"{title} Offresonance of Volume")
        output_path = os.path.join(directory, f"{title}_Volume_Histogram.png")
 
    fig.savefig(output_path, bbox_inches='tight', transparent=False)
    plt.close(fig)
    return output_path

def saveHistogramsOverlayed(directory, titles, data, slice_index):
    """Save a histogram of the data of background, estimation, actual overlayed at slice or over Full ROI to a file."""
    fig, ax = plt.subplots()
    print(F"DEBUG: saving histogram overlayed with data shape: {data.shape}, index: {slice_index}")
    if data.shape[0] == 3 or data.shape[0] == 2: # either background and est ; or back, est, and actual
        for i in range(data.shape[0]):
            makesurenottooverwrite = data[i].flatten()
            #ignore nans:
            makesurenottooverwrite = makesurenottooverwrite[~np.isnan(makesurenottooverwrite)]
            ax.hist(makesurenottooverwrite, bins=100, alpha=0.7, rwidth=0.85, label=titles[i], density=True)
    else:
        print(f"DEBUG: not expected data shape, first dimension is not 3")
        return
    ax.legend()
    ax.set_xlabel("Offresonance (Hz)")
    if slice_index >= 0:
        ax.set_title("Offresonance at Slice Index: "+str(slice_index))
        output_path = os.path.join(directory, f"overlayed_histograms_{slice_index}.png")
    else:
        ax.set_title("Offresonance Over Full ROI")
        output_path = os.path.join(directory, f"overlayed_histograms_Volume.png")
    fig.savefig(output_path, bbox_inches='tight', transparent=False)
    plt.close(fig)
    return output_path

    ##### OTHER METHODS ######

def execSSHCommand(host, hvPort, hvUser, hvPassword, command):
    # Initialize the SSH client
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # Automatically add host key
    try:
        client.connect(hostname=host, port=hvPort, username=hvUser, password=hvPassword)
        stdin, stdout, stderr = client.exec_command(command)
        return stdout.readlines()  # Read the output of the command

    except Exception as e:
        print(f"Connection or command execution failed: {e}")
    finally:
        client.close()

def execRsyncCommand(hvPass, hvUser, host, source, destination):
    # Construct the SCP command using sshpass
    cmd = f"sshpass -p {hvPass} rsync -avz {hvUser}@{host}:{source} {destination}"

    # Execute the SCP command
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Wait for the command to complete
    stdout, stderr = process.communicate()
    
    # Check if the command was executed successfully
    if process.returncode == 0:
        return stdout.decode('utf-8')
    else:
        return f"Error: {stderr.decode('utf-8')}"


def getLastSetGradients(host, hvPort, hvUser, hvPassword):
    # Command to extract the last successful setting of the shim currents
    print(f"Debug: attempting to find the last used gradients")
    command = "tail -n 100 /usr/g/service/log/Gradient.log | grep 'Prescn Success: AS Success' | tail -n 1"
    output = execSSHCommand(host, hvPort, hvUser, hvPassword, command)
    if output:
        last_line = output[0].strip()
        # Use regex to find X, Y, Z values
        match = re.search(r'X =\s+(-?\d+)\s+Y =\s+(-?\d+)\s+Z =\s+(-?\d+)', last_line)
        if match:
            gradients = [int(match.group(i)) for i in range(1, 4)]
            print(f"Debug: found that linear shims got set to {gradients}")
            return gradients
        print(f"DEBUG: no matches!")
    print(f"Debug: failed to find the last used gradients")
    return None

def setGehcExamDataPath(exam_number, host, hvPort, hvUser, hvPassword):
    output = execSSHCommand(host, hvPort, hvUser, hvPassword, "pathExtract "+exam_number)
    if output:
        last_line = output[-1].strip() 
    else:
        return None
    parts = last_line.split("/")
    return os.path.join("/", *parts[:7])

def execBashCommand(cmd):
    # Execute the bash command
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Wait for the command to complete
    stdout, stderr = process.communicate()
    
    # Check if the command was executed successfully
    if process.returncode == 0:
        return stdout.decode('utf-8')
    else:
        return f"Error: {stderr.decode('utf-8')}"

def execSCPCommand(hvPass, hvUser, hvHost, source, destination):
    # Construct the SCP command using sshpass
    cmd = f"sshpass -p {hvPass} scp -r {hvUser}@{hvHost}:{source} {destination}"

    # Execute the SCP command
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Wait for the command to complete
    stdout, stderr = process.communicate()

    # Check if the command was executed successfully
    if process.returncode == 0:
        return stdout.decode('utf-8')
    else:
        return f"Error: {stderr.decode('utf-8')}"

