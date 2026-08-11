LINIEN - RELOCK
======

This is a copy of [linien v0.3.2](https://github.com/linien-org/linien/tree/v0.3.2) that's been modified to:
- fix package dependency issues
- include a "Relock" mode that uses the RedPitaya to control a Vescent Lockbox via a TTL pulse through Analog Output 2.

Please refer to the main [linien v0.3.2 repo](https://github.com/linien-org/linien/tree/v0.3.2) for more details about the base package.

The installation has different syntax for different operating systems, so refer to the relevant section for your own system.

Installation Instructions (Mac OS)
---------------
### 0. Giving yourself a workspace

We want to keep track of where all of our files go, so first create a folder somewhere on your computer that you can access. Your Desktop is a good place. All of our other steps will be done inside this folder, so name it something descriptive like "linien_environment". Next, open a Terminal and move to there using *cd*. You can check what directory you're in with the following command in your terminal:
```bash
pwd
```

You should see the name of the folder as the last thing in this output. 

### 1. Setting up a virtual environment

Because the original linien v0.3.2 package depends on several packages that have since been updated/deprecated, the safest way to install this version of linien on a new computer is to create a virtual environment (venv). This prevents version compatibility errors between linien's required packages and any packages you might already have installed on your computer. 

Before creating the venv, check if you have Python 3.12 installed. Linien will not run on newer version of Python. In your command window type
```bash
python3.12 --version
```

If this does not return something like "Python 3.12.x", you will need to install Python 3.12. You can do this by visiting the [official Python website](https://www.python.org/downloads/release/python-3127/) and downloading the installer that's relevant for your operating system. It doesn't matter which version of Python 3.12 you install, so just install any stable release like the one in the link. Because we are on Mac, you can just scroll to the bottom of the page and click the yellow button that says "Download macOS 64-bit universal2 installer"

Now, after following the instructions in the installer, in the same terminal window run:
```bash
python3.12 --version
```

If you get a result like "Python 3.12.x" then you've successfully installed the package. 

Next, use this Python version to create a virtual environment to house all of the linien packages. 
```bash
python3.12 -m venv linienvenv
```

Attach this venv to your command window:
```bash
source linienvenv/bin/activate
```

After venv activation you should see your prompt line have a "(linienvenv)" before your file location.

Note where your virtual environment is by running this command (again) in the same command window that you just activated your virtual environment:
```bash
where python
```

Your output might contain multiple lines, but the first line should point to your virtual environment location: something like */Users/jeffreyli/Desktop/linienvenv/bin/python*. Copy this somewhere for later.

### 2. Installing linien-relock on your computer: 

Now you are (finally!) ready to install linien. First, download or clone this Github repo. There are many ways to do this, but the easiest is to navigate to the top of the page, click the green "<> Code" button, and download the zip file. 

Whatever method you use, make sure you unzip the "linien-relock" folder into your workspace folder. Inside the "linien-relock" folder, there is a file called *install_linien.sh*. Open this file in a text editor. The tenth line says
```bash
VENV="/Users/jeffreyli/Desktop/Ni Lab/linienvenv"
```

Modify this to the path for your virtual environment (DO NOT include "/bin/python") that you recorded in step 1 of the installation. Make sure you are in the linien-relock folder in your terminal (with the linienvenv virtual environment) and run the following command:
```bash
./install_linien.sh
```

This should install linien-relock and all of its necessary dependencies inside your venv

### 3. Installing linien-relock on your Red Pitaya:
First, you will need to install the correct Red Pitaya OS onto the SD card. To simplify installation (and account for the fact that not all installs of linien-relock will have internet on the Red Pitaya), I have included a complete Red Pitaya OS here on [Sharepoint](https://hu.sharepoint.com/:u:/r/sites/NiGroup/Shared%20Documents/RbCs%202026%20-%20Linked%20Files/Linien%20Relock%20OS.zip?d=w26874e108d11460991eb922e7ce85b6b&csf=1&web=1&e=GwXyEw) and here on the [RbCs Google Drive](https://drive.google.com/file/d/1xMceH1rooe4qmd-3XlVzNgDr1vGNPzwM/view?usp=drive_link)

To install the Red Pitaya OS with linien-relock already on it, unzip the "Linien Relock OS" folder and follow the instructions on the [SD card install page](https://redpitaya.readthedocs.io/en/latest/quickStart/SDcard/SDcard.html#windows-gui), selecting the unzipped "Linien Relock OS" folder in the Flash from file options. This will take ~half an hour and cannot be interrupted.

After the OS has been written to the SD card, plug the SD card into the Red Pitaya and power it on. Make sure the Red Pitaya is connected via Ethernet either directly to your computer or to a LAN network that is shared with your computer (the LAN network does not need internet access). Take note of the Red Pitaya host name, which should be written on the ethernet port and take the form of something like "RP-XXXXXX.LOCAL/". This is the IP hostname of the device and is the primary way we will connect to the device. 

In the terminal running the (linienvenv) virtual environment, type 
```bash
ping rp-XXXXXX.local
```
where you replace XXXXXX with the IP of your Red Pitaya. If you see lines appear that say something like "Reply from 192.168.x.xxx ..."  then your Red Pitaya is discoverable on your network.

Next, in the same terminal running the virtual environment, run
```bash
python
```

to create a Python screen and run the following two lines.
```python
from linien.gui.app import run_application
run_application()
```

This may take a while to open the GUI the first time. When the GUI does open, click the button to connect a New Device. Name the device something descriptive and in the line that says "rp-xxxxxx.local" enter your Red Pitaya hostname. Then, use the main GUI window to connect to the Red Pitaya.

This completes your installation of linien-relock on both your computer and the Red Pitaya


Installation Instructions (Windows)
---------------

### 0. Giving yourself a workspace

We want to keep track of where all of our files go, so first create a folder somewhere on your computer that you can access. Your Documents folder is a good place. All of our other steps will be done inside this folder, so name it something descriptive like "linien_environment". Next, open a Command Prompt and move to there using *cd*. You can check what directory you're in with the following command in your Command Prompt (NOT POWERSHELL):
```bash
cd
```

### 1. Setting up a virtual environment

Because the original linien v0.3.2 package depends on several packages that have since been updated/deprecated, the safest way to install this version of linien on a new computer is to create a virtual environment (venv). This prevents version compatibility errors between linien's required packages and any packages you might already have installed on your computer. 

Before creating the venv, check if you have Python 3.12 installed. Linien will not run on newer version of Python. Open a command window and type
```bash
py -0p
```

If this does not return a line that starts with "-V:3.12", you will need to install Python 3.12. You can do this by visiting the [official Python website](https://www.python.org/downloads/release/python-3127/) and downloading the installer that's relevant for your operating system. It doesn't matter which version of Python 3.12 you install, so just install any stable release like the one in the link. Because we are on Windows, you can just scroll to the bottom of the page and click the yellow button that says "Download Python install manager" and follow the instructions there.

After this, in the same Command Prompt window as earlier, run:
```bash
py -0
```
If you see a line that says "-V:3.12 " then you've successfully installed the package. 

Next, use this Python version to create a virtual environment to house all of the linien packages. Run:
```bash
py -3.12 -m venv linienvenv
```

Attach this venv to your command window.
```bash
linienvenv\Scripts\activate.bat
```

After venv activation you should see your prompt line have a "(linienvenv)" before your file location.

Note where your virtual environment is by running this command (again) in the same command window that you just installed your virtual environment:
```bash
where python
```

Your output might contain multiple lines, but the first line should point to your virtual environment location: something like *C:\Users\jeffreyli\Desktop\linienvenv\Scripts\python.exe*. Copy this somewhere for later.

### 2. Installing linien-relock on your computer: 

Now you are (finally!) ready to install linien. First, download or clone this Github repo. There are many ways to do this, but the easiest is to navigate to the top of the page, click the green "<> Code" button, and download the zip file. 

Whatever method you use, make sure you unzip the "linien-relock" folder into your workspace folder. Inside the "linien-relock" folder, there is a file called *install_linien.bat*. Open this file in a text editor. The ninth line says:
```bash
set VENV=C:\Users\jeffreyli\Desktop\Ni Lab\linienvenv
```

Modify this to the path for your virtual environment that you recorded in step 1 of the installation (DO NOT include "\Scripts\python.exe"). Make sure you are in the linien-relock folder in your terminal (with the linienvenv virtual environment) and run the following command:
```bash
install_linien.bat
```

### 3. Installing linien-relock on your Red Pitaya:
First, you will need to install the correct Red Pitaya OS onto the SD card. To simplify installation (and account for the fact that not all installs of linien-relock will have internet on the Red Pitaya), I have included a complete Red Pitaya OS here on [Sharepoint](https://hu.sharepoint.com/:u:/r/sites/NiGroup/Shared%20Documents/RbCs%202026%20-%20Linked%20Files/Linien%20Relock%20OS.zip?d=w26874e108d11460991eb922e7ce85b6b&csf=1&web=1&e=GwXyEw) and here on the [RbCs Google Drive](https://drive.google.com/file/d/1xMceH1rooe4qmd-3XlVzNgDr1vGNPzwM/view?usp=drive_link)

To install the Red Pitaya OS with linien-relock already on it, unzip the "Linien Relock OS" folder and follow the instructions on the [SD card install page](https://redpitaya.readthedocs.io/en/latest/quickStart/SDcard/SDcard.html#windows-gui), selecting the unzipped "Linien Relock OS" folder in the Flash from file options. This will take ~half an hour and cannot be interrupted.

After the OS has been written to the SD card, plug the SD card into the Red Pitaya and power it on. Make sure the Red Pitaya is connected via Ethernet either directly to your computer or to a LAN network that is shared with your computer (the LAN network does not need internet access). Take note of the Red Pitaya host name, which should be written on the ethernet port and take the form of something like "RP-XXXXXX.LOCAL/". This is the IP hostname of the device and is the primary way we will connect to the device. 

In the terminal running the (linienvenv) virtual environment, type 
```bash
ping rp-XXXXXX.local
```
where you replace XXXXXX with the IP of your Red Pitaya. If you see lines appear that say something like "Reply from 192.168.x.xxx ..."  then your Red Pitaya is discoverable on your network.

Next, in the same terminal running the virtual environment, run
```bash
python
```

to create a Python screen and run the following two lines.
```python
from linien.gui.app import run_application
run_application()
```

This may take a while to open the GUI the first time. When the GUI does open, click the button to connect a New Device. Name the device something descriptive and in the line that says "rp-xxxxxx.local" enter your Red Pitaya hostname. Then, use the main GUI window to connect to the Red Pitaya.

This completes your installation of linien-relock on both your computer and the Red Pitaya

Using linien-relock
---------------

### 1. Running linien-relock:

Because we are now using virtual environments, running the GUI will require first activating the appropriate virtual environment. On **Mac OS** run
```bash
source linienvenv/bin/activate
```

On **Windows** run
```bash
linienvenv\Scripts\activate.bat
```

Afterwards, the instructions are the same. Inside this virtual environment, create a python environment
```bash
python
```
And run the following two commands to bring up the GUI. 
```python
from linien.gui.app import run_application
run_application()
```

### 2. Modifying linien-relock:
If you are modifying files on your own computer, you can just run *install_linien.sh* on Mac OS and *install_linien.bat* on Windows to apply your changes.

If you are modifying files on the Red Pitaya, you will have to reboot the Red Pitaya in order for the changes to take affect. You can do this by SSH-ing into the Red Pitaya and running 
```bash
reboot
```

Tips and Tricks
---------------
The original linien 0.3.2 is a bit of a frankensteined package due to its age - some of the code packages and even some of the Python releases it uses are no longer supported by PyPI and/or modern hardware. Linien-relock tries to fix some of these problems, but it is also a series of solutions rather than a new package built from first-principles. As such, it's not inconceivable that users will find themselves needing to debug dependency problems if they make minor modifications to the code. 

At a high level, linien-relock (and linien) works in the following manner. Signals come into the Red Pitaya and are processed on the FPGA. The Python server on the Red Pitaya occasionally samples the FPGA and, based on what it sees, performs some sort of logic action. Certain parameters that the server interacts with (e.g. the Analog In 1 signal) are exposed over a network connection that your computer can read using it's own Python client. The GUI then visually formats this information into something that you can click and drag. Server-side errors will thus be displayed on the Red Pitaya and client-side errors will be displayed on your computer. 

It is recommended to first debug client-side errors, which you can do by monitoring the terminal in which you ran 
```python
from linien.gui.app import run_application
run_application()
```

Once client-side errors are addressed, you can look at server-side errors. First, connect to the Red Pitaya using the GUI. Subsequently, open another terminal window and SSH into the Red Pitaya. Run the command 
```bash
screen -ls
```

If you see something like "XXXX.spectrolockserver" then the client has connected to the Red Pitaya and started the server. You can see what the server is doing by running the command:
```bash
screen -r
```

Any problems with server-side code will be reflected in error messages in this screen. You can exit this screen by using *Ctrl + A + D*. 

If you make changes to the client side code, it is recommended that you directly edit the unzipped linien-relock folder. In this case, to activate your changes you can activate your venv and just run on Windows:
```bash
install_linien.bat
```
or on Mac:
```bash
./install_linien.sh
```
This will install all client/gui changes.

If you make any changes to the server code, the process is different because you need to write the changes onto the Red Pitaya. First, edit the code in the unzipped linien-relock folder. Then, open the appropriate bash file for your operating system: "install_relock_server.bat" for Windows and "install_relock_server.sh" for Mac. Modify the line that asks for the Red Pitaya IP. Then run the file in your terminal. On Windows:
```bash
install_relock_server.bat
```
or on Mac:
```bash
./install_relock_server.sh
```
You will be prompted for the Red Pitaya's password. The password is "root". The Red Pitaya will reboot and then you should see your server-side changes register the next time you open linien.


Uninstalling
---------------
If you ever want to uninstall linien relock, the way to do so is quite simple. Just delete the virtual environment folder ("linienvenv") and the linien-relock folder. Since everything was installed inside the virtual environment, this won't leave anything behind on your computer.