# Version Check

Check and display meshforge-maps version information.

## Instructions

1. Check version in `src/__init__.py`
2. Check `manifest.json` for plugin version
3. Show current version and status
4. Cross-check with ecosystem versions:
   - meshforge NOC: `python3 -c "import sys; sys.path.insert(0,'/opt/meshforge/src'); from __version__ import __version__; print('NOC:', __version__)"`
   - meshing_around: `grep -m1 version /opt/meshing_around_meshforge/CLAUDE.md`
