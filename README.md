# ZR4M - Garment retopo script

ZR4M stands for Zremesh for Maya. It tries to facilitate the retopology of garments by providing visual feedback and an efficient integration of Zremesh.![00_introduction_shot](./docs/media/00_introduction_shot.gif)

# How to install it

Copy this repo to the Maya script folder.
Usually has this structure:

```bash
C:\Users\$USER\Documents\maya\$MAYA_VERSION\scripts\
```

You can use this MEL command to get the exact PATH

```mel
print(`internalVar -userScriptDir`)
```

Now in Maya you can run:

```python
from ZR4M.ZR4M_ui import start_ZR4M_ui
start_ZR4M_ui()
```

Remember to use pass False inside the function if you do not want the Zbrush integration:

```python
  start_ZR4M_ui(False)
```

#### Optional step

You can import the script at startup by changing the `userSetup.py` or `userSetup.mel` inside the Maya script folder.

Append to `userSetup.py`

```python
from ZR4M.ZR4M_ui import start_ZR4M_ui
```

Or append the MEL equivalent to `userSetup.mel`

```mel
python("from ZR4M.ZR4M_ui import start_ZR4M_ui")
```

# FAQ:

- What are the requirement of this script?
  
  - Windows OS  
  - You need a Maya version that supports python 3 like Maya 2022 or greater.
  - If you want to use the Zremesh function you need to have Zbrush 2023 installed and running in the background. (Optional)

- Why not using the built in Maya Zremesh feature?
  
  - In my experience the Zremesh output result has been always better then the Maya one. With that said with the 2024 update Maya seams now able to produce good result.

- If I do not have access to Zbrush could I still use the script?    
  
  - If you cannot install Zbrush on your computer because you do own a license or simply because you are running Linux then you cannot use the Zremesh function. In those case you could still use Maya build remesh or a third party one like Quad Remesher.

- Can I do manual retopology of a piece if needed?
  
  - Yes, if you require maximum control you could start from scratch. Start by pressing "Unwrap analyze" and then instead of doing an automatic remesh do a manual one. When you finished just select the created mesh and click on "Rebind labels".
