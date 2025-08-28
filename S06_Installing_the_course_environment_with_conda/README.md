# Installing the course environment(s) with conda
Gareth Funning, University of California, Riverside

To use the course materials past the end of the course and closing of access to OpenScienceLab, you will need to install an environment that can support the various software packages and tools that the course makes use of. Luckily for us, it only takes a few simple `conda` (or `conda`'s faster cousin, `mamba`) commands to get the job done.

These instructions aasume that you have an operating system with terminal access $-$ a Linux operating system, a Windows operating system with the WSL (Windows Subsystem for Linux) installed, or MacOS.

## 1. Install miniforge
`miniforge` is the fully open source cousin of the `miniconda` package installer. It draws natively from the open source conda-forge package repository, in which many of the major packages we use in the course reside.

This is easy enough to do! Simply go to the `miniforge` Github site ([https://github.com/conda-forge/miniforge](https://github.com/conda-forge/miniforge)) and follow the instructions there for the operating system of your choice. (For WSL, you should download the Linux version, which will work fine in WSL.)
IF you already have `miniconda` installed, you can still use it, but I highly recommend installing `mamba` to use with it if you haven't already.

## 2. Download the environment file
The various environments that we used in the course are available for download from the ASF's GitHub: [https://github.com/ASFOpenSARlab/opensarlab-envs/tree/main/Environment_Configs](https://github.com/ASFOpenSARlab/opensarlab-envs/tree/main/Environment_Configs)

You will see files in that directory that correspond to the environments we used in the course $-$ `earthscope_insar_env.yml` (the exact same environment we use for most of the Jupyter notebooks), `autorift_env.yml` (the environment used in the pixel offsets exercise), and `earthscope_dolphin_env.yml` (the environment used in the Dolphin exercise). Download the one(s) you want to install.

**Optional, but you'll probably want to do it**: If you want to use the course Jupyter notebooks, you will probably want to edit the environment file of your choice to add `jupyter` to the list of packages under `dependencies:`. 

## 3. Install the environment using mamba
Navigate to the location of your downloaded environment file, and run a command like this:
```
mamba env create -f earthscope_insar_env.yml
```
(Or substitute the name of the environment file you'd like to install instead. If, for some inexplicable reason, you didn't install `mamba` then you can run the above command with `conda` instad of `mamba`, but it will take a lot longer.)

## 4. Finish up your setup
Several of the packages you have installed expect files to be on your path. So we can write some configuration scripts that set up those variables.

### For ISCE
Using your favorite text editor, make a file called `ISCE_config.sh` in a place where you can access it easily, containing the below:
```
export PATH=$ISCE_HOME/applications:$PATH
```

### For mintpy
Post-installation setup instructions can be found here: [https://github.com/insarlab/MintPy/blob/main/docs/installation.md](https://github.com/insarlab/MintPy/blob/main/docs/installation.md)

## 5. Let's go!
Assuming that everything worked, then you can start to use your new environment. First you need to activate it:
```
conda activate earthscope_insar
```
Next you need to run your configuration script (include the path to the file in this command if necessary):
```
source ISCE_config.sh
```
And finally, go to a directory containing Jupyter notebooks and open Jupyter!
```
jupyter notebook
```

## 6. You want to download all of the Jupyter notebooks used in the course
Navigate to the place on your file system where you want to store them, and run this to clone them:
```
git clone https://github.com/isceplus/2025-isceplus.git
```
The place where you download these would probably be a good place to start up Jupyter!

## 7. You want to download the isce2 source code
This can be useful for using the stack processors.

Navigate to the place you would like to store the files, then clone the repository:
```
git clone https://github.com/isce-framework/isce2
```
Details on the stack processors can be found here: https://github.com/isce-framework/isce2/tree/main/contrib/stack

...or in the `isce2/contrib/stack` directory of the files you just cloned.

## Repositories of commonly-used packages
For more details of packages and their installation, go to the source!
* isce2: https://github.com/isce-framework/isce2
* mintpy: https://github.com/insarlab/MintPy
* autoRIFT: https://github.com/nasa-jpl/autoRIFT
* Dolphin: https://github.com/isce-framework/dolphin
* isce3: https://github.com/isce-framework/isce3

