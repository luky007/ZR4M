import itertools
import math
import os
import subprocess
import time
from io import TextIOWrapper
from pathlib import Path
from random import random
from typing import Dict, List, Optional, Set, Tuple, Union, overload

import maya.api.OpenMaya as om2
import maya.cmds as cmds
import maya.mel as mel
from maya import OpenMayaUI
from PySide2.QtCore import QCoreApplication, QPoint, QSize, Qt, QTimer, Signal
from PySide2.QtGui import QColor, QCursor, QPainter, QPen
from PySide2.QtWidgets import (QApplication, QCheckBox, QDockWidget,
                               QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLayout, QMainWindow,
                               QPushButton, QSizePolicy, QSlider, QSpacerItem,
                               QSpinBox, QVBoxLayout, QWidget)
from shiboken2 import wrapInstance

# For running this code just paste it into the Maya console.

#
# What this program tries to accomplish:
#
# - Create an interface that facilities the use of the Zremesh in Maya.
# Note that a Zbrush needs to installed and also needs to have a valid license.
#
# - Speed up the garments retopo workflow.
# It can unwrap mesh in addition to providing valuable information to the user.
# The process is similar to the one that can be found in the UV editor
# by enabling the "Show UV border". Note that the UV point that are displayed with a
# gradient are referenced by this code with the name "UV master point".
#

#
# This code uses connection between nodes for avoid storing variables in python.
# This allow the program to works properly in different sessions.
# This is the list of all the connections:
#
# - cmds.listConnections(f"{curve}.connection_between_perimeter_and_pointer_curve")
# Used for updating the labels and for deselecting. The transform node of a perimeter
# curve is connected to the transform node of the pointer curve.
# If the perimeter curve is queried you can get either the transform node of the pointer
# curve or None if that curve on the perimeter is not paired.
# If the pointer curve is queried you can should always get two transforms node
# of the perimeter curves.
#
# - cmds.listConnections(f"{curve}.connection_between_label_and_perimeter_curve")
# Used for updating the labels and for deselecting. The transform node of a perimeter
# curve is connected to the transform node of the label curve.
# If the perimeter curve is queried you should always get the transform node of the
# label curve and vice-versa.
#
# - cmds.listConnections(f"{curve or set}.connection_between_quick_set_and_label_curve")
# Used for updating the labels. The transform node of a label curve is connected to
# the quick set.
# If the label curve is queried you should get the quick set node and vice-versa.
# If the user delete the mesh with some quick sets than they will be deleted also.
#
# - cmds.getAttr(f"{curve}.connection_between_float_indicator_and_label_curve")
# Used for updating the labels. The transform node of a label curve is connected to
# the indicator of the paramDimension node.
# You can easily query is float value or setting a new one.
#
# - cmds.listConnections(f"{curve}.connection_between_indicator_and_label_curve")
# Used for deselecting. The transform node of a label curve is connected to
# the transform of the label indicator.
# If the label curve is queried you should always get the transform node of a label curve
# and vice-versa.
# This one is only used for make it a bit easier to find the label indicator but is optional.
#

def duplicate_mesh_without_set(
    geometry_to_duplicate: Union[str, List[str], Set[str]],
    name_duplicate: str = None,
) -> List[str]:
    """Utility function to duplicate a specified geometry without maintaining the set selection.

    Args:
        geometry_to_duplicate (Union[str,List[str],Set[str]]): input geometries to duplicate.
        name_duplicate (str, optional): Give a name to the duplicates. Defaults to None.

    Returns:
        List[str]: the list of duplicate nodes.
    """

    if name_duplicate:
        output_node = cmds.duplicate(
            geometry_to_duplicate, name=name_duplicate)
    else:
        output_node = cmds.duplicate(geometry_to_duplicate)

    for node in output_node:
        list_all_quick_set = (set(cmds.listSets(object=node, extendToShape=1))
                              - set(cmds.listSets(
                                    object=node, extendToShape=1, type=1)))
        for quick_set in list_all_quick_set:
            cmds.sets(f"{node}", rm=quick_set)
            cmds.sets(f"{node}.vtx[*]", rm=quick_set)
            cmds.sets(f"{node}.e[*]", rm=quick_set)
            cmds.sets(f"{node}.f[*]", rm=quick_set)
            cmds.sets(f"{node}.vtxFace[*]", rm=quick_set)
            cmds.sets(f"{node}.map[*]", rm=quick_set)

    return output_node


def return_curve_in_scene() -> Tuple[List[str], List[str]]:
    """Return the list of all the curves transform nodes as well as shapes nodes.

    Returns:
        Tuple[List[str],List[str]]: the transform nodes list, the shapes nodes list.
    """
    list_curve_shape_in_scene = cmds.ls(type="nurbsCurve")
    list_curve_in_scene = []
    for curve in list_curve_shape_in_scene:
        name_curve = cmds.listRelatives(
            curve, path=True, parent=True).pop()
        list_curve_in_scene.append(name_curve)

    return list_curve_in_scene, list_curve_shape_in_scene


@overload
def get_index_component(input_component: str) -> int:
    pass


@overload
def get_index_component(
    input_component: Union[List[str], Set[str]]
) -> Set[int]:
    pass


def get_index_component(
    list_component: Union[str, Union[List[str], Set[str]]]
) -> Union[int, Set[int]]:
    """Extract the index number from a component name. NOTE: vtxFace components are not supported.

    Args:
        input_component (str) or (list): the name of the component to extract like "pCube1.vtx[100]"

    Raises:
        TypeError: components given must be explicit. Es: "pCube.map[100:150]" need to be flattened.

    Returns:
        Union[int, Set[int]: the index number of the component.
        If multiple str were given a set of int will be returned.
    """

    if isinstance(list_component, str):
        list_component = {list_component}

    result = set()
    for component in list_component:
        index_part = component.split("[")[1]
        if any(index_component in index_part for index_component in (":", "*")):
            raise TypeError(
                f"The input selection appears to not have been flattened. Found: {component}"
            )

        result.add(int(index_part[:-1]))

    if len(result) == 1:
        return result.pop()

    return result


@overload
def add_full_name_to_index_component(
    input_index_component: int,
    geometry_name: str,
    mode: str,
) -> str:
    pass


@overload
def add_full_name_to_index_component(
    input_index_component: Union[List[int], Set[int]],
    geometry_name: str,
    mode: str,
) -> Set[str]:
    pass


def add_full_name_to_index_component(
    input_index_component: Union[int, Union[List[int], Set[int],]],
    geometry_name: str,
    mode: str,
) -> Union[str, Set[str]]:
    """Return the full component name from a component index.
    NOTE: vtxFace components are not supported.

    Args:
        input_index_component (int or list): Works with a single integer or with a list of integers.
        geometry_name (str): A component name like "pCube1".
        mode (str): One of the component names supported by Maya: [vtx, e, f, map].

    Returns:
        Union[str, Set[str]]: The component name as str or as a set in multiple input were given.
    """
    if isinstance(input_index_component, int):
        return f"{geometry_name}.{mode}[{int(input_index_component)}]"

    result = set()
    for component in input_index_component:
        result.add(f"{geometry_name}.{mode}[{int(component)}]")
    return result


def get_closest_vertex(
    geometry_name: Union[str, om2.MFnMesh],
    input_position: Tuple[int, int, int] = (0, 0, 0),
) -> Tuple[int, float]:
    """Return the closest vertex and distance from mesh to world space input_position [x, y, z]
    Uses om2.MfnMesh.getClosestPoint() returned face ID and iterates through face's vertices

    Args:
        geometry_name(str) or (om2.MFnMesh): whenever you can is preferable to pass the om2.MFnMesh.
        Its avoid to create a new one
        input_position (tuple, optional): Inside you can put the a 3D input_position in world space.
        Defaults to (0,0,0).

    Raises:
        TypeError: The geometry_name variable must be either a string or a om2.MFnMesh

    Returns:
        Tuple[int, float]: The first value is the index of the closest vertex.
        The second one is the distance from the given input_position
    """

    if isinstance(geometry_name, str):
        selection_list = om2.MSelectionList()
        selection_list.add(geometry_name)
        mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))

    elif isinstance(geometry_name, om2.MFnMesh):
        mfn_mesh = geometry_name

    else:
        raise TypeError(
            "The geometry_name variable must be either a string or a om2.MFnMesh"
        )

    input_position = om2.MPoint(input_position)
    index_closest_face = mfn_mesh.getClosestPoint(
        input_position, space=om2.MSpace.kWorld
    )[1]
    list_index_vtx_in_face = mfn_mesh.getPolygonVertices(index_closest_face)

    vertex_distances = set()
    for vtx in list_index_vtx_in_face:
        point = mfn_mesh.getPoint(vtx, om2.MSpace.kWorld)
        distance = point.distanceTo(input_position)
        vertex_distances.add((vtx, distance))

    closest_distance = float("inf")
    closest_vtx_index = -1
    for pair in vertex_distances:
        distance = pair[1]
        if distance < closest_distance:
            closest_vtx_index = pair[0]
            closest_distance = distance

    return closest_vtx_index, closest_distance


def message(text: str, raise_error: bool, stay_time: int = 3000) -> None:
    """Display a message to the user.

    Args:
        text (str): the text to display at the center of the screen.
        raise_error (bool): specifies if the message should be an error message or just a warning.
        time (int, optional): the amount of time the message should stay at maximum opacity.
        Defaults to 3000 ms.
    """

    cmds.inViewMessage(message=text, pos="midCenter",
                       fade=True, fadeStayTime=stay_time)

    if raise_error is True:
        cmds.error(text)
    else:
        cmds.warning(text)


def raise_error_if_mesh_has_overlapping_uvs(geometry_name: str) -> None:
    """Raise an error if the mesh has overlapping uvs.

    Args:
        geometry_name (str): the name of the geometry to check
    """
    if find_overlapping_uvs(geometry_name, output_bool=True) is True:
        cmds.select(find_overlapping_uvs(geometry_name, output_bool=False))
        # the overlapping UV are a problem because later the "unwrap" function will later merge
        # all the vtx and UV with a threshold of 0.
        message(
            f"The mesh: {geometry_name} appears to have overlapping UV. Unwrap the UV or sew cuts",
            raise_error=True)


def raise_error_if_mesh_has_missing_uvs(geometry_name: str) -> None:
    """Raise an error if the mesh has missing uvs.

    Args:
        geometry_name (str): the name of the geometry to check
    """
    # BUG: not important if uv is < than vtx. Use api to check if every vtx as at least 1 UV
    if cmds.polyEvaluate(geometry_name, uv=True) < cmds.polyEvaluate(
        geometry_name, vertex=True
    ):
        message(
            f"Some part of the mesh: {geometry_name} appears to be without UV",
            raise_error=True)
        return False


def raise_error_if_mesh_has_one_uv_shell(geometry_name: str) -> None:
    """Raise an error if the mesh one uv shell.

    Args:
        geometry_name (str): the name of the geometry to check
    """
    if get_shell_ids(geometry_name)[0] <= 1:
        # the script would just create a single line along all the UV border. its useless to do.
        message(
            f"The mesh: {geometry_name} needs to have at least two uv shell. Found only one",
            raise_error=True)
        return False


def raise_error_if_mesh_has_unpairable_uv_border(geometry_name: str) -> None:
    """Raise an error if the mesh uv border are not pairable.

    Args:
        geometry_name (str): the name of the geometry to check
    """
    if cmds.polyEvaluate(geometry_name, uv=True) == cmds.polyEvaluate(
        geometry_name, vertex=True
    ):
        cmds.polyMergeVertex(
            geometry_name, constructionHistory=False, distance=0.0)
        if cmds.polyEvaluate(geometry_name, uv=True) == cmds.polyEvaluate(
            geometry_name, vertex=True
        ):
            # the mesh has been flatten. The goal here is to calculate the "Master UV points"
            # so the UV islands need to connected in 3D space.
            cmds.select(geometry_name)
            message(
                f"The UV island of the mesh: {geometry_name} are not connected in 3D space",
                raise_error=True)


def raise_error_if_mesh_is_unflat(geometry_name: str) -> None:
    """Raise an error if the mesh is not flat by one axis.

    Args:
        geometry_name (str): the name of the geometry to check
    """

    bbox = cmds.exactWorldBoundingBox(geometry_name, calculateExactly=True)
    size_x = abs(abs(bbox[3])-abs(bbox[0]))
    size_y = abs(abs(bbox[4])-abs(bbox[1]))
    size_z = abs(abs(bbox[5])-abs(bbox[2]))

    threshold_absolute_size = 0.01
    list_flatten_axises = []
    if size_x < threshold_absolute_size:
        list_flatten_axises.append("x")
    if size_y < threshold_absolute_size:
        list_flatten_axises.append("y")
    if size_z < threshold_absolute_size:
        list_flatten_axises.append("z")

    if len(list_flatten_axises) == 0:
        message(
            f"The mesh: {geometry_name} appears to not be flat {size_x,size_y,size_z}",
            raise_error=True)
    if len(list_flatten_axises) > 1:
        message(
            f"The mesh: {geometry_name} is flat by {str(len(list_flatten_axises))} axises",
            raise_error=True)


def get_current_selected_mesh(
    complain_if_more_selected: bool,
    complain_if_none_selected: bool,
) -> Union[str, List[str]]:
    """Check the selected object and output only object that are mesh.

    Args:
        complain_if_more_selected (bool): raise error if more than one mesh is selected.
        complain_if_none_selected (bool): raise error if no mesh is selected.

    Returns:
        Union[str, Set[str]]: if complain_if_more_selected is True than a string is returned
        otherwise a set of strings.
    """

    list_mesh_selected = []
    list_all_selected_objects = cmds.ls(selection=True, flatten=True)

    for obj in list_all_selected_objects:
        shape_node = cmds.listRelatives(obj, shapes=True, path=True)
        if shape_node is not None:
            # if the current obj selected has a shape node and its type is "mesh" then obj is a mesh
            if cmds.objectType(shape_node[0], isType="mesh"):
                list_mesh_selected.append(obj)

    if complain_if_none_selected is True:
        if len(list_all_selected_objects) == 0:
            message("You have selected nothing", raise_error=True)
        if len(list_mesh_selected) == 0:
            message("Your selection do not contain meshes", raise_error=True)

    if complain_if_more_selected is True:
        if len(list_mesh_selected) > 1:
            message("Select only one mesh", raise_error=True)
        else:
            return list_mesh_selected.pop()

    if len(list_mesh_selected) == 1:
        return list_mesh_selected.pop()
    return list_mesh_selected


def flatten_selection_list(
    selection_list: Union[str, Union[List[str], Set[str]]]
) -> Optional[Set[str]]:
    """Flatten a selection list. Es: cube_GEO.vtx[1:3] --> [cube.vtx[1],cube.vtx[2],cube.vtx[3]]
    NOTE: vtxFace components are not supported as well as component with "*" inside.

    Args:
        selection_list (str) or (list): a list of components or a single one always in str format.

    Raises:
        TypeError: if "*" expression is found inside the component name. Not supported.

    Returns:
        Set[str]: a set of components that have been flattened.
    """

    if selection_list is None:
        return None

    if isinstance(selection_list, str):
        selection_list = {selection_list}

    flat_list = set()
    for component in selection_list:
        name_component,  index_part = component.split("[")
        if not any(index_component in index_part for index_component in (":", "*")):
            flat_list.add(component)
            continue
        elif "*" in index_part:
            raise TypeError(
                f"The '*' expression is not supported. {component}")
            # component_type = name_component[ name_component.rfind(".") + 1 :]
        elif ":" in index_part:
            index_start = component.find("[") + 1
            index_end = component.rfind(":")

            begin = int(component[index_start:index_end])
            end = int(
                component[index_end + 1: component.find("]", index_start)])

            for number in range(begin, end + 1):
                flat_list.add(f"{name_component}[{number}]")

    return flat_list


def find_overlapping_uvs(
    geometry_name: Union[str, om2.MFnMesh], output_bool: bool
) -> Union[bool, Set[str]]:
    """Checks if the given geometry has overlapping UVs.
    Returns either a boolean or a list of overlapping UV points.

    Args:
        geometry_name (str) or (om2.MFnMesh): The name of the geometry to check for overlapping UVs.
        output_bool (bool): If True returns a boolean indicating whether overlapping UVs were found.
        If False, returns a list of strings representing the UV points that overlap.
        If no overlapping UVs are found returns None.

    Returns:
        Union[bool, List[str]: If output_bool True returns if the given geometry has overlapping UV.
        If output_bool is False, returns a list of strings representing the UV points that overlap.
    """

    if isinstance(geometry_name, str):
        selection_list = om2.MSelectionList()
        selection_list.add(geometry_name)
        mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))

    elif isinstance(geometry_name, om2.MFnMesh):
        mfn_mesh = geometry_name

    mfn_mesh.getUVs()
    u_array, v_array = mfn_mesh.getUVs()

    if output_bool is True:  # when finds the first intersection return True right away
        list_uv = set()
        for u_cord, v_cord in zip(u_array, v_array):
            uv_cord = (u_cord, v_cord)
            if uv_cord in list_uv:
                return True
            list_uv.add(uv_cord)
        return False

    else:
        list_uv = set()
        list_uv_overlapping = set()
        for u_cord, v_cord, index in zip(u_array, v_array, range(len(u_array))):
            uv_cord = (u_cord, v_cord)
            if uv_cord in list_uv:
                list_uv_overlapping.add(index)
            list_uv.add(uv_cord)

        list_index_uv_overlapping = {
            f"{geometry_name}.map[{uv_index}]" for uv_index in list_uv_overlapping
        }
        return list_index_uv_overlapping


def get_shell_ids(
    geometry_name: Union[str, om2.MFnMesh]
) -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """Get UV shell information for a given geometry.

    Args:
        geometry_name (Union[str, om2.MFnMesh]):The name of the geometry to check.

    Raises:
        TypeError: The geometry_name variable must be either a string or a om2.MFnMesh

    Returns:
        (Tuple): Returns a tuple containing describing how the specified UV set's UVs
        are grouped into shells. The first element of the tuple is the number
        of distinct shells. The second element of the tuple is an array of
        shell indices, one per uv, indicating which shell that uv is part of.
    """

    if isinstance(geometry_name, str):
        selection_list = om2.MSelectionList()
        selection_list.add(geometry_name)
        mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))

    elif isinstance(geometry_name, om2.MFnMesh):
        mfn_mesh = geometry_name

    else:
        raise TypeError(
            "The geometry_name variable must be either a string or a om2.MFnMesh"
        )

    return mfn_mesh.getUvShellsIds()  # type: ignore [no-any-return]


def get_component_on_border(
    geometry_name: str, mode: str
) -> Union[Tuple[int], Tuple[int, ...], Set[int]]:
    """Given the name of a mesh transform or shape,
    this function will return the component that live on the border.
    NOTE: When the mode "UV" is selected the default UV map set will always be used.

    Args:
        geometry_name (str): The name of the geometry to check.
        mode (str): defines with component to check.
        Possible values are [ 'vtx' , 'edge' , 'face' , 'UV' ]

    Raises:
        ValueError: Wrong mode selected.

    Returns:
        Union[Tuple[int], Tuple[int, ...]: a series of integers
        or multiple series of integers in "uv" mode is selected
    """

    selection_list = om2.MSelectionList()
    selection_list.add(geometry_name)

    if mode == "edge":
        mfn_mesh_edge = om2.MItMeshEdge(selection_list.getDagPath(0))

        list_index_edge_on_border = set()
        while not mfn_mesh_edge.isDone():
            if mfn_mesh_edge.onBoundary():
                edge_index = mfn_mesh_edge.index()
                list_index_edge_on_border.add(edge_index)
            mfn_mesh_edge.next()

        return list_index_edge_on_border

    elif mode == "vtx":
        mfn_mesh_vtx = om2.MItMeshVertex(selection_list.getDagPath(0))

        list_index_vtx_on_border = set()
        while not mfn_mesh_vtx.isDone():
            if mfn_mesh_vtx.onBoundary():
                vtx_index = mfn_mesh_vtx.index()
                list_index_vtx_on_border.add(vtx_index)
            mfn_mesh_vtx.next()

        return list_index_vtx_on_border

    elif mode == "face":
        mfn_mesh_face = om2.MItMeshPolygon(selection_list.getDagPath(0))

        list_index_face_on_border = set()
        while not mfn_mesh_face.isDone():
            if mfn_mesh_face.onBoundary():
                face_index = mfn_mesh_face.index()
                list_index_face_on_border.add(face_index)
            mfn_mesh_face.next()

        return list_index_face_on_border

    elif mode == "UV":
        # How this function find the UV that live on the border:
        # For every edge it store the connected faces and the vertex of that very edge.
        # Then get the UV from the vertex of the faces that are connected to that very edge.
        #
        # Note: You need to query the UV points of a face and not of a vertex because otherwise
        # it may give you some UV that are not connected to the edge
        #
        # A bit of theory:
        #
        # The number of UV connection of a face will always be equal to the number of vtx in a face.
        #
        # You can find an edge that live on the UV border if that has one the following specs:
        # - It has more than two UV connection
        # - it has less than two connected faces

        mfn_mesh_edge = om2.MItMeshEdge(selection_list.getDagPath(0))
        mfn_mesh_face = om2.MItMeshPolygon(selection_list.getDagPath(0))

        dict_edge = {}
        dict_face: Dict[int, Tuple[List[int], List[int]]] = {}
        list_index_uv_on_border = set()
        list_index_edge_on_uv_border = set()
        list_index_vtx_on_uv_border = set()
        list_index_face_on_uv_border = set()

        while not mfn_mesh_edge.isDone():
            dict_edge[mfn_mesh_edge.index()] = set(mfn_mesh_edge.getConnectedFaces()), [
                mfn_mesh_edge.vertexId(0),
                mfn_mesh_edge.vertexId(1),
            ]
            mfn_mesh_edge.next()
        while not mfn_mesh_face.isDone():
            vtx_connected_to_face = mfn_mesh_face.getVertices()
            uv_connected_to_face = []
            for i in range(len(vtx_connected_to_face)):
                uv_connected_to_face.append(mfn_mesh_face.getUVIndex(i))

            dict_face[mfn_mesh_face.index()] = (
                mfn_mesh_face.getVertices(),
                uv_connected_to_face,
            )
            mfn_mesh_face.next()
        for edge, value_edge in dict_edge.items():
            is_edge_on_border = None
            uv_connected_to_edge = set()
            face_connected_to_edge = value_edge[0]
            vertexes_edge = value_edge[1]

            for face in face_connected_to_edge:
                vertexes_face = dict_face.get(face)[0]
                for i, vtx in enumerate(vertexes_face):
                    if vtx in vertexes_edge:
                        uv_point = dict_face.get(face)[1][i]
                        uv_connected_to_edge.add(uv_point)

            if len(uv_connected_to_edge) > 2:
                is_edge_on_border = True

            elif len(face_connected_to_edge) < 2:
                is_edge_on_border = True

            if is_edge_on_border:
                for uv_index in uv_connected_to_edge:
                    list_index_uv_on_border.add(uv_index)
                for face in face_connected_to_edge:
                    list_index_face_on_uv_border.add(face)
                list_index_vtx_on_uv_border.update(vertexes_edge)
                list_index_edge_on_uv_border.add(edge)

        return (
            list_index_uv_on_border,
            list_index_vtx_on_uv_border,
            list_index_edge_on_uv_border,
            list_index_face_on_uv_border,
        )

    else:
        raise ValueError(
            "Wrong mode selected. Choose between: [ 'edge' , 'vtx' , 'face' , 'uv' ] not: ",
            mode,
        )


def get_neighbors_uv_on_border(
    geometry_name: str,
    list_edge_on_uv_border_index: Set[int],
    list_face_on_uv_border_index: Set[int],
    list_uv_on_border_index: Set[int],
) -> Dict[int, Tuple[int, int]]:
    """Given the name of a mesh return the uv neighbors of every uv point.

    Args:
        geometry_name (str): The name of the geometry to check.
        list_edge_on_uv_border_index (Set[int]): sequence of edge index that live on the uv border.
        list_face_on_uv_border_index (Set[int]): sequence of face index that live on the uv border.
        list_uv_on_border_index (Set[int]): sequence of indices of uv that live on the uv border

    Raises:
        RuntimeError: Expected other value. The logic needs refinement.

    Returns:
        dictionary: the key value is the index of a uv point that live on the uv border.
        the value is a tuple containing the index of the other two uv neighbors
    """

    # How this function works:
    # For every edge of the mesh
    # it get the two connected edge that also live on the uv border.
    # Process the edges in two couple: AB & AC.
    # For both edge couple get all the faces that are connected to both edges.
    #
    # 1 case: For every face found, if all 3 vtx that make up the edge couple are also in that face
    # then the uv of vtx that the edge couple has in common will be the "uv master point"
    # and the uv of the other 2 vtx will considered neighbor.
    #
    # 2 case: For every face ask if another face has 1 uv point in common between each other.
    # If it does that uv point will be the "Master uv point".
    # The two neighbor will be the uv point that are on the 2 vtx at the far end of the edge couple.
    # or
    # if three neighbors are found then try to find the the two vtx in common between the two faces.
    # get the uv point of those two vtx and subtract the master uv point to the uv point just found.

    selection_list = om2.MSelectionList()
    selection_list.add(geometry_name)
    mfn_mesh_edge = om2.MItMeshEdge(selection_list.getDagPath(0))
    mfn_mesh_face = om2.MItMeshPolygon(selection_list.getDagPath(0))

    dict_face = {}
    dict_edge = {}
    dict_neighbor_uv = {}

    while not mfn_mesh_edge.isDone():
        current_edge_index = mfn_mesh_edge.index()

        if current_edge_index in list_edge_on_uv_border_index:
            dict_edge[mfn_mesh_edge.index()] = (
                set(mfn_mesh_edge.getConnectedFaces()),
                set(mfn_mesh_edge.getConnectedEdges()),
                [mfn_mesh_edge.vertexId(0), mfn_mesh_edge.vertexId(1)],
            )

        mfn_mesh_edge.next()

    while not mfn_mesh_face.isDone():
        current_face_index = mfn_mesh_face.index()

        if current_face_index in list_face_on_uv_border_index:
            vtx_connected_to_face = mfn_mesh_face.getVertices()
            uv_connected_to_face = []

            for i in range(len(vtx_connected_to_face)):
                uv_connected_to_face.append(mfn_mesh_face.getUVIndex(i))

            dict_face[mfn_mesh_face.index()] = (
                mfn_mesh_face.getVertices(),
                uv_connected_to_face,
            )

        mfn_mesh_face.next()

    mfn_mesh_edge = om2.MItMeshEdge(selection_list.getDagPath(0))
    # mfn_mesh_edge.reset()
    list_edge_done = set()
    while not mfn_mesh_edge.isDone():
        current_edge_index = mfn_mesh_edge.index()
        if current_edge_index in list_edge_on_uv_border_index:
            connected_edges = dict_edge.get(current_edge_index)[1]
            connected_edges_on_border = {
                edge for edge in connected_edges if edge in list_edge_on_uv_border_index
            }

            for other_edge in connected_edges_on_border:
                vtx_current_edge = set(dict_edge.get(current_edge_index)[2])
                vtx_other_edge = set(dict_edge.get(other_edge)[2])
                vtx_on_edges = vtx_other_edge.union(vtx_current_edge)
                common_vtx_edges = vtx_other_edge & vtx_current_edge
                common_vtx_edges = common_vtx_edges.pop()

                face_connected_to_current_edge = dict_edge.get(current_edge_index)[
                    0]
                face_connected_to_edges = face_connected_to_current_edge.union(
                    dict_edge.get(other_edge)[0]
                )

                if (
                    current_edge_index in list_edge_done
                    and other_edge in list_edge_done
                ):
                    uv_master_points = set()
                    for face in face_connected_to_edges:
                        vertexes_current_face = dict_face.get(face)[0]
                        uv_current_face = dict_face.get(face)[1]
                        for i, vtx in enumerate(vertexes_current_face):
                            if vtx == common_vtx_edges:
                                uv_master_points.add(uv_current_face[i])
                    uv_neighbor_already_found = True
                    for uv_index in uv_master_points:
                        if not uv_index in dict_neighbor_uv:
                            uv_neighbor_already_found = False
                    if uv_neighbor_already_found is True:
                        continue

                for face in face_connected_to_edges:
                    vertexes_current_face = dict_face.get(face)[0]
                    list_other_faces = set(face_connected_to_edges)
                    list_other_faces.remove(face)
                    common_vtx_face = set(
                        vertexes_current_face
                    ) & vtx_current_edge.union(vtx_other_edge)

                    if len(common_vtx_face) == 3:
                        uv_current_face = dict_face.get(face)[1]
                        neighbor_master_uv_point = set()
                        counter_master_uv_point = 0
                        for vtx_e in vtx_on_edges:
                            for i, vtx_f in enumerate(vertexes_current_face):
                                if vtx_e == vtx_f:
                                    local_id = i
                                    if vtx_e == common_vtx_edges:
                                        master_uv_point = uv_current_face[local_id]
                                        counter_master_uv_point += 1
                                    else:
                                        neighbor_master_uv_point.add(
                                            uv_current_face[local_id]
                                        )

                        dict_neighbor_uv[master_uv_point] = list(
                            neighbor_master_uv_point
                        )

                    for other_face in list_other_faces:
                        uv_current_face = dict_face.get(face)[1]
                        uv_filtered_current_face = set()
                        for vtx_e in vtx_on_edges:
                            for i, vtx_f in enumerate(vertexes_current_face):
                                local_id = -1
                                if vtx_e == vtx_f:
                                    local_id = i
                                    break
                            if local_id != -1:
                                uv_filtered_current_face.add(
                                    uv_current_face[local_id])

                        vtx_other_face = dict_face.get(other_face)[0]
                        uv_other_face = dict_face.get(other_face)[1]
                        uv_filtered_other_face = set()
                        for vtx_e in vtx_on_edges:
                            for i, vtx_f in enumerate(vtx_other_face):
                                local_id = -1
                                if vtx_e == vtx_f:
                                    local_id = i
                                    break
                            if local_id != -1:
                                uv_filtered_other_face.add(
                                    uv_other_face[local_id])

                        common_uv = uv_filtered_other_face & uv_filtered_current_face
                        if len(common_uv) == 1:
                            master_uv_point = common_uv.pop()
                            neighbor_master_uv_point = uv_filtered_other_face.union(
                                uv_filtered_current_face
                            )
                            neighbor_master_uv_point.remove(master_uv_point)
                            if len(neighbor_master_uv_point) == 2:
                                dict_neighbor_uv[master_uv_point] = list(
                                    neighbor_master_uv_point
                                )
                                list_edge_done.add(current_edge_index)
                                list_edge_done.add(other_edge)
                            elif len(neighbor_master_uv_point) == 3:
                                common_vtx_face = set(vtx_other_face) & set(
                                    vertexes_current_face
                                )
                                common_uv = {
                                    uv_current_face[i]
                                    for vtx in common_vtx_face
                                    for i, vtx_f in enumerate(vertexes_current_face)
                                    if vtx == vtx_f
                                }
                                common_uv = common_uv.union(
                                    {
                                        uv_other_face[i]
                                        for vtx in common_vtx_face
                                        for i, vtx_f in enumerate(vtx_other_face)
                                        if vtx == vtx_f
                                    }
                                )

                                neighbor_master_uv_point = common_uv - \
                                    {master_uv_point}
                                dict_neighbor_uv[master_uv_point] = list(
                                    neighbor_master_uv_point
                                )

        mfn_mesh_edge.next()

    if len(dict_neighbor_uv) != len(list_uv_on_border_index):
        list_uv_found = {uv_index for uv_index in dict_neighbor_uv}
        list_uv_not_found = {
            f"{geometry_name}.map[{uv_index}]"
            for uv_index in list_uv_on_border_index - list_uv_found
        }
        cmds.select(list_uv_not_found)
        raise RuntimeError("No neighbor for the selected uv point")

    return dict_neighbor_uv


def get_cord_uv_master_point_posed_mesh(
    geometry_name: str,
    dict_neighbor_uv_on_border: Dict[int, Tuple[int, int]],
    list_vertex_index_on_border: Set[int],
) -> Dict[int, Tuple[float, float]]:
    """Returns the coordinates of the master uv point found in the specified mesh.

    Args:
        geometry_name (str): The name of the geometry to check. Its cannot be uv flattened.
        dict_neighbor_uv_on_border (Dict[int, Tuple[int, int]]): the two corresponding uv neighbors
        for every uv that live on the uv border.
        list_vertex_index_on_border (Set[int]): series of vertex that live on the mesh border.

    Returns:
        Dict[int, Tuple[float, float]]: the key is the index of the uv master point found.
        The value is the corresponding coordinate of that point.
    """

    # for every vtx on the uv index border get its uv points.
    selection_list = om2.MSelectionList()
    selection_list.add(geometry_name)
    mfn_vtx = om2.MItMeshVertex(selection_list.getDagPath(0))
    mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))

    dict_vtx_uv = {}
    while not mfn_vtx.isDone():
        dict_vtx_uv[mfn_vtx.index()] = set(mfn_vtx.getUVIndices())
        mfn_vtx.next()

    # The dictionary became uv index centric instead of vtx index centric.
    dict_uv_vtx = {}
    for vtx, uv_indexes in dict_vtx_uv.items():
        for uv_index in uv_indexes:
            if not uv_index in dict_uv_vtx:
                dict_uv_vtx[uv_index] = len(uv_indexes), vtx

    dict_cord_master_uv_point = {}
    for uv_index, uv_neighbors in dict_neighbor_uv_on_border.items():
        # it no use to re-find it again.
        if uv_index in dict_cord_master_uv_point:
            continue

        is_master_uv_point = False
        uv_neighbor_00, uv_neighbor_01 = uv_neighbors
        n_connection_target = dict_uv_vtx.get(uv_index)[0]
        n_connection_neighbor_00 = dict_uv_vtx.get(uv_neighbor_00)[0]
        n_connection_neighbor_01 = dict_uv_vtx.get(uv_neighbor_01)[0]

        if n_connection_target >= 3:
            is_master_uv_point = True

        if (
            n_connection_target > n_connection_neighbor_00
            or n_connection_target > n_connection_neighbor_01
        ):
            is_master_uv_point = True

        # if the current vtx is on the border and one or more neighbor vtx are not then ignore it.
        if is_master_uv_point is False:
            vtx_neighbor_00 = dict_uv_vtx.get(uv_neighbor_00)[1]
            vtx_neighbor_01 = dict_uv_vtx.get(uv_neighbor_01)[1]
            current_vtx = dict_uv_vtx.get(uv_index)[1]

            if current_vtx in list_vertex_index_on_border:
                if (
                    not vtx_neighbor_00 in list_vertex_index_on_border
                    or not vtx_neighbor_01 in list_vertex_index_on_border
                ):
                    is_master_uv_point = True

        if is_master_uv_point is True:
            current_vtx = dict_uv_vtx.get(uv_index)[1]
            all_uv_current_vtx = dict_vtx_uv.get(current_vtx)
            for uv_index in all_uv_current_vtx:
                uv_cord = mfn_mesh.getUV(uv_index)
                dict_cord_master_uv_point[uv_index] = uv_cord

    return dict_cord_master_uv_point


def unwrap(mesh_to_unwrap: str, list_vertex_on_uv_border_index: Set[int]) -> str:
    """Unwrap the given geometry.

    Args:
        mesh_to_unwrap (str): the name of the geometry.
        list_vertex_on_uv_border_index (Set[int]): series of vertex that live on the uv border.

    Raises:
        Exception: Vertex failed detaching.

    Returns:
        str: name of the newly created unwrapped geometry.
    """

    unwrapped_geo = duplicate_mesh_without_set(
        mesh_to_unwrap, name_duplicate=f"{mesh_to_unwrap.split('|')[-1] }_unwrapped")[0]

    vtx_to_detach = [
        f"{unwrapped_geo}.vtx[{str(vtx)}]" for vtx in list_vertex_on_uv_border_index
    ]

    # leave selected the detach vertexes
    cmds.polySplitVertex(vtx_to_detach, constructionHistory=False)
    vtx_on_border_unwrapped_geo = cmds.ls(selection=True, flatten=True)

    if cmds.polyEvaluate(unwrapped_geo, uv=True) != cmds.polyEvaluate(
        unwrapped_geo, vertex=True
    ):
        raise RuntimeError(
            f"the mesh: {unwrapped_geo} appears to not be flatten")

    selection_list = om2.MSelectionList()
    selection_list.add(unwrapped_geo)
    mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))
    mfn_vtx = om2.MItMeshVertex(selection_list.getDagPath(0))

    list_coordinate_uv_point = mfn_mesh.getUVs()
    point_array = om2.MFloatPointArray()
    while not mfn_vtx.isDone():
        uv_index = mfn_vtx.getUVIndices()[0]
        u_cord = list_coordinate_uv_point[0][uv_index]
        v_cord = list_coordinate_uv_point[1][uv_index]
        point_array.append(om2.MFloatPoint(u_cord, v_cord, 0))
        mfn_vtx.next()

    mfn_mesh.setPoints(point_array)
    cmds.polyMergeVertex(
        vtx_on_border_unwrapped_geo, distance=0, constructionHistory=False
    )
    cmds.polyMergeUV(vtx_on_border_unwrapped_geo, constructionHistory=False)
    cmds.polyNormalPerVertex(unwrapped_geo, xyz=(0, 0, 1))

    # maintain position and scale ratio
    if not cmds.getAttr(f"{mesh_to_unwrap}.visibility"):
        cmds.setAttr(f"{mesh_to_unwrap}.visibility", 1)
    if not cmds.getAttr(f"{unwrapped_geo}.visibility"):
        cmds.setAttr(f"{unwrapped_geo}.visibility", 1)
    # center pivot only works if the mesh is visible
    cmds.xform(mesh_to_unwrap, centerPivots=1)
    cmds.xform(unwrapped_geo, centerPivots=1)

    mesh_to_unwrap_bb = cmds.exactWorldBoundingBox(
        mesh_to_unwrap, calculateExactly=True)
    unwrapped_geo_bb = cmds.exactWorldBoundingBox(
        unwrapped_geo, calculateExactly=True)

    mesh_to_unwrap_bb += [abs(mesh_to_unwrap_bb[0] - mesh_to_unwrap_bb[3]), abs(
        mesh_to_unwrap_bb[1] - mesh_to_unwrap_bb[4]),
        abs(mesh_to_unwrap_bb[2] - mesh_to_unwrap_bb[5])]
    mesh_to_unwrap_bb.append(
        max(mesh_to_unwrap_bb[6], mesh_to_unwrap_bb[7], mesh_to_unwrap_bb[8]))
    unwrapped_geo_bb += [abs(unwrapped_geo_bb[0] - unwrapped_geo_bb[3]), abs(
        unwrapped_geo_bb[1] - unwrapped_geo_bb[4]), abs(unwrapped_geo_bb[2] - unwrapped_geo_bb[5])]
    unwrapped_geo_bb.append(
        max(unwrapped_geo_bb[6], unwrapped_geo_bb[7], unwrapped_geo_bb[8]))
    ratio_scale = 1 * mesh_to_unwrap_bb[6] / unwrapped_geo_bb[6]

    cmds.scale(ratio_scale, ratio_scale, ratio_scale,
               unwrapped_geo, absolute=True)
    cmds.move(0.5 * (mesh_to_unwrap_bb[0] + mesh_to_unwrap_bb[3]), 0.5 * (mesh_to_unwrap_bb[1] +
              mesh_to_unwrap_bb[4]), 0.5 * (mesh_to_unwrap_bb[2] + mesh_to_unwrap_bb[5]),
              unwrapped_geo, absolute=True)
    cmds.makeIdentity(
        unwrapped_geo, apply=True, translate=True, rotate=True, scale=True, preserveNormals=True
    )  # if not inaccurate curve couple binding (mfn_curve.closestPoint()) not sure why.

    cmds.select(clear=True)
    return unwrapped_geo


def dict_uv_cord_to_compare_to(
    geometry_name: str, list_uv_on_border_index: Union[None, Set[int]] = None
) -> Dict[int, Tuple[float, float]]:
    """Given a mesh return the coordinates of the uv point that live on the uv border.

    Args:
        geometry_name (str): the name of the geometry.
        list_uv_on_border_index (Union[None, Set[int]], optional): sequence of indices of uv
        that live on the uv border. Defaults to None.

    Raises:
        ValueError: Unable to determine the uv point that live on the uv border because the geometry
        appears to not have been flattened by the uv.

    Returns:
        Dict[int, Tuple[float, float]]: the key is the index of a uv point
        that live on the uv border. The value is the corresponding coordinate of that point.
    """

    if cmds.polyEvaluate(geometry_name, uv=True) == cmds.polyEvaluate(
        geometry_name, vertex=True
    ):
        is_mesh_uv_flatten = True
    else:
        is_mesh_uv_flatten = False

    if is_mesh_uv_flatten is False and list_uv_on_border_index is None:
        raise ValueError(
            "The list of uv that live on the UV border needs to be provided."
        )

    selection_list = om2.MSelectionList()
    selection_list.add(geometry_name)
    mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))
    list_coordinate_uv_point = mfn_mesh.getUVs()
    if is_mesh_uv_flatten is True:
        mfn_mesh_vtx = om2.MItMeshVertex(selection_list.getDagPath(0))
        list_uv_on_border_index = set()
        while not mfn_mesh_vtx.isDone():
            if mfn_mesh_vtx.onBoundary():
                for uv_index in mfn_mesh_vtx.getUVIndices():
                    list_uv_on_border_index.add(uv_index)
            mfn_mesh_vtx.next()

    dict_uv_to_compare_to = {}
    for uv_index in list_uv_on_border_index:
        dict_uv_to_compare_to[uv_index] = (
            list_coordinate_uv_point[0][uv_index],
            list_coordinate_uv_point[1][uv_index],
        )

    return dict_uv_to_compare_to


def re_find_uv_master_point(
    dict_cord_master_uv_point: Dict[int, Tuple[float, float]],
    dict_uv_to_compare_to: Dict[int, Tuple[float, float]],
    discard_threshold: float = 0.000000001
) -> Set[int]:
    """Re-find the UV master point on the flatten or posed version of the same mesh.

    Args:
        dict_cord_master_uv_point (Dict[int,Tuple[float,float]]): the key is the index of the uv
        master point found. The value is the corresponding coordinate of that point.
        dict_uv_to_compare_to (Dict[int,Tuple[float,float]]): the key is the index of a uv point
        that live on the uv border. The value is the corresponding coordinate of that point.

    Returns:
        Set[int]: The series of newly found uv master points.
    """

    newly_found_uv_master_point = set()
    for uv_cord in dict_cord_master_uv_point.keys():
        target_u_cord, target_v_cord = dict_cord_master_uv_point.get(uv_cord)
        closest_uv, closest_distance = None, float("inf")
        for uv_index in dict_uv_to_compare_to.keys():
            u_cord, v_cord = dict_uv_to_compare_to.get(uv_index)

            distance = (u_cord - target_u_cord) ** 2 + \
                (v_cord - target_v_cord) ** 2
            if distance < closest_distance:
                closest_distance = distance
                closest_uv = uv_index

        if closest_distance < discard_threshold:
            newly_found_uv_master_point.add(closest_uv)

    return newly_found_uv_master_point


def calculate_path_between_two_uv_on_border(
    start_index_uv: int,
    end_index_uv: int,
    dict_neighbor_uv_on_border: Dict[int, Tuple[int, int]],
    shell_ids: List[int],
    list_stop_point: Set[int] = None,
) -> Union[Tuple[Set[int]], Tuple[Set[int], Set[int]], None]:
    """Return the path between two given uv points that live on the uv border.

    Args:
        start_index_uv (int): uv point from with the walk along the uv border will start from.
        end_index_uv (int): uv point to reach during the walk.
        dict_neighbor_uv_on_border (Dict[int, Tuple[int, int]]): the two corresponding uv neighbors
        for every uv that live on the uv border.
        shell_ids (List[int]): defines which shell every uv point live
        list_stop_point (Set[int], optional): sequence of uv points that live on the uv border that
        will break the walking loop. Defaults to None.

    Raises:
        RuntimeError: the neighbors information are missing inside the dictionary.
        RuntimeError: the while loop appears to be endless.

    Returns:
        Union[ Tuple[int, Set[int]] , Tuple[int, Set[int], Set[int]], Tuple[Set[int]], None]: if the
        path is not possible return None. Otherwise return one or two tuple containing the path.
    """

    if list_stop_point is None:
        list_stop_point = set()

    if (
        not start_index_uv in dict_neighbor_uv_on_border.keys()
        or not end_index_uv in dict_neighbor_uv_on_border.keys()
    ):
        raise RuntimeError(
            "impossible to find the UV neighbor of the input UV point: ",
            [start_index_uv, end_index_uv],
        )

    if shell_ids[start_index_uv] != shell_ids[end_index_uv]:
        return None  # The two UV points are not even on the same UV shell

    list_every_input_point = list_stop_point.union(
        {start_index_uv, end_index_uv})

    if start_index_uv != end_index_uv:
        stop_while_loop = False
        is_first_side_ok = False
        is_second_side_ok = False
        uv_point_loop_00 = set({start_index_uv, end_index_uv})
        uv_point_loop_01 = set({start_index_uv, end_index_uv})

        key_uv_point = dict_neighbor_uv_on_border.get(start_index_uv)[
            0
        ]  # start with one direction
        if not key_uv_point in list_every_input_point:
            list_current_neighbors = dict_neighbor_uv_on_border.get(
                key_uv_point)
            old_uv_point = start_index_uv
            which_side_loop = 0
            uv_point_loop_00.add(key_uv_point)

        else:  # first side is done now try from the other side
            if end_index_uv == key_uv_point:
                is_first_side_ok = True

            key_uv_point = dict_neighbor_uv_on_border.get(start_index_uv)[
                1
            ]  # try with other direction
            if (
                not key_uv_point in list_every_input_point
                and start_index_uv != end_index_uv
            ):
                list_current_neighbors = dict_neighbor_uv_on_border.get(
                    key_uv_point)
                old_uv_point = start_index_uv
                which_side_loop = 1
                uv_point_loop_01.add(key_uv_point)
            else:  # both sides already done
                stop_while_loop = True
                if end_index_uv == key_uv_point:
                    is_second_side_ok = True

        counter = 0
        while stop_while_loop is False:
            if counter == 10000:
                raise RuntimeError("Counter safe limit")
            counter += 1
            if end_index_uv in list_current_neighbors:
                if which_side_loop == 0:
                    is_first_side_ok = True  # the first direction has been a success
                    which_side_loop = 1
                    uv_point_loop_00.add(key_uv_point)

                    key_uv_point = dict_neighbor_uv_on_border.get(start_index_uv)[
                        1]
                    list_current_neighbors = dict_neighbor_uv_on_border.get(
                        key_uv_point
                    )
                    old_uv_point = start_index_uv
                    uv_point_loop_01.add(key_uv_point)

                    if key_uv_point in list_every_input_point:
                        if end_index_uv == key_uv_point:
                            is_second_side_ok = True
                            stop_while_loop = True
                    continue
                else:
                    uv_point_loop_01.add(key_uv_point)
                    is_second_side_ok = True  # the second direction has been a success
                    stop_while_loop = True
                    continue

            if (
                list_current_neighbors[0] in list_stop_point
                or list_current_neighbors[1] in list_stop_point
            ):
                if which_side_loop == 0:
                    which_side_loop = 1  # the first direction has failed
                    key_uv_point = dict_neighbor_uv_on_border.get(start_index_uv)[
                        1]
                    list_current_neighbors = dict_neighbor_uv_on_border.get(
                        key_uv_point
                    )
                    old_uv_point = start_index_uv
                    uv_point_loop_01.add(key_uv_point)

                    if key_uv_point in list_every_input_point:
                        stop_while_loop = True
                        if end_index_uv == key_uv_point:
                            is_second_side_ok = True
                            stop_while_loop = True

                    continue

                else:
                    stop_while_loop = True  # the second direction has failed
                    continue

            # if nothing can stop the loop then continue to walk the UV shell border
            if old_uv_point in list_current_neighbors:
                dummy_var = key_uv_point
                key_uv_point = (set(list_current_neighbors) -
                                {old_uv_point}).pop()
                list_current_neighbors = dict_neighbor_uv_on_border.get(
                    key_uv_point)
                old_uv_point = dummy_var

                if which_side_loop == 0:
                    uv_point_loop_00.add(key_uv_point)
                else:
                    uv_point_loop_01.add(key_uv_point)
        if is_first_side_ok is True and is_second_side_ok is True:
            return uv_point_loop_00, uv_point_loop_01
        elif is_first_side_ok is True and is_second_side_ok is False:
            return uv_point_loop_00
        elif is_first_side_ok is False and is_second_side_ok is True:
            return uv_point_loop_01
        else:
            return None

    else:  # if the two input index UV point are the same then walk in only the first direction
        key_uv_point = dict_neighbor_uv_on_border.get(start_index_uv)[0]
        if key_uv_point in list_every_input_point:
            return None
        old_uv_point = start_index_uv
        list_current_neighbors = dict_neighbor_uv_on_border.get(key_uv_point)
        uv_point_loop = set({start_index_uv, end_index_uv})

        stop_while_loop = False
        counter = 0
        while stop_while_loop is False:
            if counter == 10000:
                raise RuntimeError("Counter safe limit")
            counter += 1
            if end_index_uv in list_current_neighbors:
                if (
                    counter != 1
                ):  # without this the code would stop walking right after the start
                    uv_point_loop.add(key_uv_point)
                    return uv_point_loop

            if (
                list_current_neighbors[0] in list_stop_point
                or list_current_neighbors[1] in list_stop_point
            ):
                return None

            # if nothing can stop the loop then continue to walk the UV shell border
            if old_uv_point in list_current_neighbors:
                dummy_var = key_uv_point
                key_uv_point = (set(list_current_neighbors) -
                                {old_uv_point}).pop()
                list_current_neighbors = dict_neighbor_uv_on_border.get(
                    key_uv_point)
                old_uv_point = dummy_var
                uv_point_loop.add(old_uv_point)


@overload
def create_curve(
    geometry_name: str,
    list_input_master_uv_point: Set[int],
    just_return_list_edge_loop_full_name: False
) -> Set[str]:
    pass


@overload
def create_curve(
    geometry_name: str,
    list_input_master_uv_point: Set[int],
    just_return_list_edge_loop_full_name: True
) -> List[Set[int]]:
    pass


def create_curve(
    geometry_name: str,
    list_input_master_uv_point: Set[int],
    just_return_list_edge_loop_full_name: bool
) -> Union[Set[str], List[Set[str]]]:
    """Creates curves along the perimeter of the flatten mesh.

    Args:
        geometry_name (str): the name of the geometry.
        list_input_master_uv_point (Set[int]): list of master uv point that are on the flatten mesh.

    Raises:
        RuntimeError: stuck in the loop that create circular closed curves.

    Returns:
        Set[str] or List[Set[str]]: name of the created curves or
        the full name of the edges that make the edges loop path.
    """

    # This function will create all the curve on the perimeter of the mesh that has been UV flatten.
    # For each possible combination of two input master UV point will check if the two point are
    # topologically the closest to each other. If so a curve will be created from that edge path.
    #
    # If there are un-found UV it means that one or more shell are circle.
    # They can have just one UV master point or even none.
    # If walking on all the UV shell border the only UV master point found is the input one
    # then save the edge path an create a circular curve

    list_component_on_uv_border = get_component_on_border(
        geometry_name, mode="UV")
    (
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    ) = (
        list_component_on_uv_border[2],
        list_component_on_uv_border[3],
        list_component_on_uv_border[0],
    )
    dict_neighbor_uv_on_border = get_neighbors_uv_on_border(
        geometry_name,
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    )
    shell_ids = get_shell_ids(geometry_name)[1]
    list_index_uv_found = set()
    dict_edge_loop_path = {}

    for uv_pair in itertools.combinations(list_input_master_uv_point, 2):
        list_index_uv_path = calculate_path_between_two_uv_on_border(
            uv_pair[0],
            uv_pair[1],
            dict_neighbor_uv_on_border,
            shell_ids,
            list_input_master_uv_point - {uv_pair[0], uv_pair[1]},
        )
        if list_index_uv_path is None:
            continue
        if isinstance(list_index_uv_path, tuple):
            path_00 = add_full_name_to_index_component(
                list_index_uv_path[0], geometry_name, "map")
            path_00 = cmds.polyListComponentConversion(
                path_00,
                fromUV=True,
                toEdge=True,
                internal=True)
            path_00 = flatten_selection_list(path_00)
            dict_edge_loop_path[f"{uv_pair[0]} , {uv_pair[1]}"] = path_00
            path_01 = add_full_name_to_index_component(
                list_index_uv_path[1], geometry_name, "map")
            path_01 = cmds.polyListComponentConversion(
                path_01,
                fromUV=True,
                toEdge=True,
                internal=True)
            path_01 = flatten_selection_list(path_01)
            dict_edge_loop_path[f"{uv_pair[0]} , {uv_pair[1]} , {2}"] = path_01
            list_index_uv_found.update(
                list_index_uv_path[0], list_index_uv_path[1])
        if isinstance(list_index_uv_path, set):
            path_00 = add_full_name_to_index_component(
                list_index_uv_path, geometry_name, "map")
            path_00 = cmds.polyListComponentConversion(
                path_00,
                fromUV=True,
                toEdge=True,
                internal=True)
            path_00 = flatten_selection_list(path_00)
            dict_edge_loop_path[f"{uv_pair[0]} , {uv_pair[1]}"] = path_00
            list_index_uv_found.update(list_index_uv_path)

    # some UV point are undone. Do all the circular curve
    if len(list_uv_on_border_index) != len(list_index_uv_found):
        list_index_uv_not_found = list_uv_on_border_index - list_index_uv_found
        counter = 0
        while len(list_index_uv_not_found) != 0:
            for uv_index in list_index_uv_not_found:
                list_index_uv_path = calculate_path_between_two_uv_on_border(
                    uv_index, uv_index, dict_neighbor_uv_on_border, shell_ids
                )
                path_00 = add_full_name_to_index_component(
                    list_index_uv_path, geometry_name, "map")
                path_00 = cmds.polyListComponentConversion(
                    path_00,
                    fromUV=True,
                    toEdge=True,
                    internal=True,
                )
                path_00 = flatten_selection_list(path_00)
                dict_edge_loop_path[uv_index] = path_00
                list_index_uv_not_found = list_index_uv_not_found - list_index_uv_path
                break
            if counter == 10000:
                raise RuntimeError("Infinite loop")
            else:
                counter += 1

    list_created_curve = set()
    list_edge_loop_path = []
    for uv_point in dict_edge_loop_path:
        edge_loop_path = dict_edge_loop_path.get(uv_point)
        list_edge_loop_path.append(edge_loop_path)
        if just_return_list_edge_loop_full_name is False:
            cmds.select(edge_loop_path) # NOTE in maya 24 you need to select it before polyToCurve
            output_curve = cmds.polyToCurve(
                form=2,
                degree=1,
                name=f"{geometry_name.split('|')[-1]}_perimeter_01",
                conformToSmoothMeshPreview=1,
                constructionHistory=False,
            )[0]
            list_created_curve.add(output_curve)

    if just_return_list_edge_loop_full_name:
        return list_edge_loop_path
    return list_created_curve


def find_closest_cord_uv_point_on_mesh_based_on_curve(
    geometry_name: str,
    input_curve: str,
    mfn_mesh_unwrapped_geo: om2.MFnMesh,
) -> Dict[int, Tuple[float, float]]:
    """Return the closest UV point from a given curve and mesh.

    Args:
        geometry_name (str): the name of the geometry.
        input_curve (str): the name of the curve.
        mfn_mesh_unwrapped_geo (om2.MFnMesh): the MFnMesh of the unwrapped geometry to reuse

    Returns:
        Dict[int, Tuple[float, float]]: the key value is the index of the uv found and
        the value are its coordinates.
    """

    # if a curve as only two .ep than get the coordinate of the uv point
    # closest to ep[0] and ep[1].
    # else get the coordinate of the uv point closest to ep[1] because you are sure
    # that that uv point sit on a vtx that only as two UV connection.

    n_of_ep_on_curve = cmds.getAttr(f"{input_curve}.spans")
    if n_of_ep_on_curve > 1:
        pos_first_ep = cmds.pointPosition(f"{input_curve}.ep[1]", world=True)
        index_vtx_01_on_unwrapped_geo = get_closest_vertex(
            mfn_mesh_unwrapped_geo,
            input_position=[pos_first_ep[0], pos_first_ep[1], pos_first_ep[2]],
        )[0]
        vtx_01_on_unwrapped_geo = add_full_name_to_index_component(
            index_vtx_01_on_unwrapped_geo, geometry_name, "vtx"
        )
        uv_01_on_unwrapped_geo = cmds.polyListComponentConversion(
            vtx_01_on_unwrapped_geo, fromVertex=True, toUV=True
        )[0]
        index_uv_01_on_unwrapped_geo = get_index_component(
            uv_01_on_unwrapped_geo)

        dict_cord_uv_points_closest_to_curve = {
            index_uv_01_on_unwrapped_geo: mfn_mesh_unwrapped_geo.getUV(
                index_uv_01_on_unwrapped_geo
            )
        }

        return dict_cord_uv_points_closest_to_curve

    else:
        pos_first_ep = cmds.pointPosition(f"{input_curve}.ep[0]", world=True)
        index_vtx_01_on_unwrapped_geo = get_closest_vertex(
            mfn_mesh_unwrapped_geo,
            input_position=[pos_first_ep[0], pos_first_ep[1], pos_first_ep[2]],
        )[0]
        vtx_01_on_unwrapped_geo = add_full_name_to_index_component(
            index_vtx_01_on_unwrapped_geo, geometry_name, "vtx"
        )
        uv_01_on_unwrapped_geo = cmds.polyListComponentConversion(
            vtx_01_on_unwrapped_geo, fromVertex=True, toUV=True
        )[0]
        index_uv_01_on_unwrapped_geo = get_index_component(
            uv_01_on_unwrapped_geo)

        pos_second_ep = cmds.pointPosition(f"{input_curve}.ep[1]", world=True)
        index_vtx_02_on_unwrapped_geo = get_closest_vertex(
            mfn_mesh_unwrapped_geo,
            input_position=[pos_second_ep[0],
                            pos_second_ep[1], pos_second_ep[2]],
        )[0]
        vtx_02_on_unwrapped_geo = add_full_name_to_index_component(
            index_vtx_02_on_unwrapped_geo, geometry_name, "vtx"
        )
        uv_02_on_unwrapped_geo = cmds.polyListComponentConversion(
            vtx_02_on_unwrapped_geo, fromVertex=True, toUV=True
        )[0]
        index_uv_02_on_unwrapped_geo = get_index_component(
            uv_02_on_unwrapped_geo)

        dict_cord_uv_points_closest_to_curve = {
            index_uv_01_on_unwrapped_geo: mfn_mesh_unwrapped_geo.getUV(
                index_uv_01_on_unwrapped_geo
            ),
            index_uv_02_on_unwrapped_geo: mfn_mesh_unwrapped_geo.getUV(
                index_uv_02_on_unwrapped_geo
            ),
        }

        return dict_cord_uv_points_closest_to_curve


def bind_curve(
    posed_geometry: om2.MFnMesh,
    unwrapped_geo: om2.MFnMesh,
    list_all_created_curve: Set[str],
    list_uv_on_border_index: Set[int],
) -> Tuple[Set[str], Set[str], Set[str]]:
    """Pair every curve in couple and apply styles to them.

    Args:
        posed_geometry (om2.MFnMesh): the MFnMesh of the posed geometry to reuse.
        unwrapped_geo (om2.MFnMesh): the MFnMesh of the unwrapped geometry to reuse.
        list_all_created_curve (Set[str]): set of all created curves.
        list_uv_on_border_index (Set[int]): sequence of uv that live on the uv border.

    Raises:
        RuntimeError: Some curve pairs with more than one.

    Returns:
        Tuple[Set[str], Set[str], Set[str]]: the first set containing all the curve.
        The second all the curve on the perimeter and the last one all the pointer curves.
    """
    # TODO fix .overrideColor limit the number of couple to 32.

    list_close_curve = set()
    list_open_curve = set()
    list_pair_curve = []
    for curve in list_all_created_curve:
        form = cmds.getAttr(f"{curve}.form")
        if form == 0:  # the curve is open
            list_open_curve.add(curve)
        else:
            list_close_curve.add(curve)

    selection_list = om2.MSelectionList()
    selection_list.add(posed_geometry)
    selection_list.add(unwrapped_geo)
    mfn_mesh_posed_geometry = om2.MFnMesh(selection_list.getDagPath(0))
    mfn_mesh_unwrapped_geo = om2.MFnMesh(selection_list.getDagPath(1))
    mfn_vtx_posed_geometry = om2.MItMeshVertex(selection_list.getDagPath(0))
    dict_vtx_connection = {}

    while not mfn_vtx_posed_geometry.isDone():
        dict_vtx_connection[mfn_vtx_posed_geometry.index()] = set(
            mfn_vtx_posed_geometry.getUVIndices()
        )
        mfn_vtx_posed_geometry.next()

    dict_uv_connection = {}
    for vtx, indexes_uv_point in dict_vtx_connection.items():
        for uv_index in indexes_uv_point:
            if not uv_index in dict_uv_connection:
                dict_uv_connection[uv_index] = vtx

    dict_closest_uv_to_curve = {}
    for curve in list_all_created_curve:
        dict_cord_uv_points_closest_to_curve = (
            find_closest_cord_uv_point_on_mesh_based_on_curve(
                unwrapped_geo, curve, mfn_mesh_unwrapped_geo
            )
        )
        list_uv_point_on_posed_mesh = re_find_uv_master_point(
            dict_cord_uv_points_closest_to_curve,
            dict_uv_cord_to_compare_to(
                posed_geometry, list_uv_on_border_index),
        )
        list_all_uv_connection = set()
        for uv_index in list_uv_point_on_posed_mesh:
            list_all_uv_connection.update(
                dict_vtx_connection.get(dict_uv_connection.get(uv_index))
            )

        dict_closest_uv_to_curve[curve] = (
            list_uv_point_on_posed_mesh,
            list_all_uv_connection,
        )

    curve_with_at_least_three_ep = set()
    curve_with_two_ep = set()
    for curve in list_all_created_curve:
        if len(dict_closest_uv_to_curve.get(curve)[0]) == 1:
            curve_with_at_least_three_ep.add(curve)
        else:
            curve_with_two_ep.add(curve)

    for curve in curve_with_two_ep:
        couple_uv_index, all_uv_indexes = dict_closest_uv_to_curve.get(curve)
        list_uv_to_search_couple_in = all_uv_indexes - couple_uv_index
        if len(list_uv_to_search_couple_in) >= 2:
            for curve_02 in list_open_curve:
                if curve == curve_02:
                    continue

                common_uv = (
                    list_uv_to_search_couple_in
                    & dict_closest_uv_to_curve.get(curve_02)[0]
                )

                if len(common_uv) == 2:
                    pair_already_exist = False
                    for pair in list_pair_curve:
                        if curve in pair or curve_02 in pair:
                            if pair != {curve, curve_02}:
                                raise RuntimeError(
                                    "Pair already done with another",
                                    pair,
                                    {curve, curve_02},
                                )
                        if pair == {curve, curve_02}:
                            pair_already_exist = True
                    if not pair_already_exist:
                        list_pair_curve.append({curve, curve_02})

    for curve in curve_with_at_least_three_ep:
        direct_uv_index, all_uv_indexes = dict_closest_uv_to_curve.get(curve)
        if all_uv_indexes == direct_uv_index:
            continue
        other_uv = (all_uv_indexes - direct_uv_index).pop()
        dict_uv_cord = {other_uv: mfn_mesh_posed_geometry.getUV(other_uv)}
        uv_point_on_unwrapped_geo = (
            re_find_uv_master_point(
                dict_uv_cord, dict_uv_cord_to_compare_to(unwrapped_geo)
            )
        ).pop()
        uv_point_full_name_on_unwrapped_geo = add_full_name_to_index_component(
            uv_point_on_unwrapped_geo, unwrapped_geo, "map"
        )
        vtx_point_full_name_on_unwrapped_geo = cmds.polyListComponentConversion(
            uv_point_full_name_on_unwrapped_geo, fromUV=True, toVertex=True
        )[0]
        vtx_point_on_unwrapped_geo = get_index_component(
            vtx_point_full_name_on_unwrapped_geo
        )
        pos_vtx_point_on_unwrapped_geo = mfn_mesh_unwrapped_geo.getPoint(
            vtx_point_on_unwrapped_geo, space=om2.MSpace.kWorld
        )

        closest_curve = None
        closest_distance = float("inf")
        for curve_02 in list_all_created_curve:
            selection_list = om2.MSelectionList()
            selection_list.add(curve_02)
            mfn_curve = om2.MFnNurbsCurve(selection_list.getDagPath(0))

            distance = mfn_curve.distanceToPoint(
                pos_vtx_point_on_unwrapped_geo, space=om2.MSpace.kWorld
            )
            if distance < closest_distance:
                closest_curve = curve_02
                closest_distance = distance

        pair_already_exist = False
        for pair in list_pair_curve:
            if curve in pair or closest_curve in pair:
                if pair != {curve, closest_curve}:
                    raise RuntimeError(
                        "Pair already done with another", pair, {
                            curve, closest_curve}
                    )
            if pair == {curve, closest_curve}:
                pair_already_exist = True
        if not pair_already_exist:
            list_pair_curve.append({curve, closest_curve})

    # now define the aesthetical attributes
    for curve in list_all_created_curve:
        curve_shape_node = cmds.listRelatives(
            curve, shapes=True, path=True)[0]
        cmds.setAttr(curve_shape_node + ".overrideColor", 1)  # set black color
        cmds.setAttr(curve_shape_node + ".overrideEnabled", 1)
        cmds.setAttr(curve_shape_node + ".lineWidth", 5)
        cmds.setAttr(curve_shape_node + ".lineWidth", 5)
        # cmds.setAttr(curve_shape_node + ".overrideDisplayType", 1)

        dummy_attr_perimeter_pointer_curve = (
            "connection_between_perimeter_and_pointer_curve"
        )  # use during the mouse evaluation

        cmds.addAttr(
            curve,
            longName=dummy_attr_perimeter_pointer_curve,
            attributeType="long",
            min=0,
            max=0,
            defaultValue=0,
            hidden=True,
        )

    color_index = 4  # I do not like the other color that is all
    list_pointer_curve_created = set()
    for pair in list_pair_curve:
        couple_perimeter_curve = list(pair)

        selection_list = om2.MSelectionList()
        selection_list.add(couple_perimeter_curve[0])
        selection_list.add(couple_perimeter_curve[1])

        mfn_curve_00 = om2.MFnNurbsCurve(selection_list.getDagPath(0))
        bounding_box_00 = mfn_curve_00.boundingBox
        mfn_curve_01 = om2.MFnNurbsCurve(selection_list.getDagPath(1))
        bounding_box_01 = mfn_curve_01.boundingBox
        bounding_box_00_center = bounding_box_00.center
        bounding_box_01_center = bounding_box_01.center
        center_point = om2.MPoint(
            (bounding_box_00_center.x + bounding_box_01_center.x) / 2.0,
            (bounding_box_00_center.y + bounding_box_01_center.y) / 2.0,
            (bounding_box_00_center.z + bounding_box_01_center.z) / 2.0,
        )
        distance = bounding_box_00_center.distanceTo(bounding_box_01_center)
        scaled_distance = distance * 0.9  # optional but nicer to see
        scaled_point_00 = center_point + (
            (bounding_box_00_center - center_point) * scaled_distance / distance
        )
        scaled_point_01 = center_point + (
            (bounding_box_01_center - center_point) * scaled_distance / distance
        )

        pointer_curve = cmds.curve(
            degree=1,
            name=f"{posed_geometry.split('|')[-1]}_pointer_01",
            point=[
                (scaled_point_00[0], scaled_point_00[1], scaled_point_00[2]),
                (scaled_point_01[0], scaled_point_01[1], scaled_point_01[2]),
            ],
        )
        cmds.setAttr(f"{pointer_curve}.visibility", 0)
        new_curve_shape_node_name = cmds.listRelatives(
            pointer_curve, shapes=True, path=True)[0]
        list_pointer_curve_created.add(pointer_curve)
        cmds.setAttr(new_curve_shape_node_name + ".overrideColor", 1)
        cmds.setAttr(new_curve_shape_node_name + ".overrideEnabled", 1)
        cmds.setAttr(new_curve_shape_node_name + ".lineWidth", 5)

        curve_shape_node = cmds.listRelatives(
            couple_perimeter_curve[0], shapes=True, path=True)[0]
        twin_curve_shape_node = cmds.listRelatives(
            couple_perimeter_curve[1], shapes=True, path=True)[0]
        cmds.setAttr(curve_shape_node + ".overrideColor", color_index)
        cmds.setAttr(twin_curve_shape_node + ".overrideColor", color_index)
        cmds.setAttr(new_curve_shape_node_name + ".overrideColor", color_index)
        color_index += 1

        cmds.addAttr(
            pointer_curve,
            longName=dummy_attr_perimeter_pointer_curve,
            attributeType="long",
            min=0,
            max=0,
            defaultValue=0,
            hidden=True,
        )
        cmds.connectAttr(
            f"{pointer_curve}.{dummy_attr_perimeter_pointer_curve}",
            f"{couple_perimeter_curve[0]}.{dummy_attr_perimeter_pointer_curve}",
        )
        cmds.connectAttr(
            f"{pointer_curve}.{dummy_attr_perimeter_pointer_curve}",
            f"{couple_perimeter_curve[1]}.{dummy_attr_perimeter_pointer_curve}",
        )

    list_perimeter_curve = list_all_created_curve
    list_all_created_curve = list_all_created_curve.union(list_perimeter_curve)

    dummy_circle = cmds.circle(name="dummy_circle", ch=False)[0]
    for curve_to_sweep in list_perimeter_curve:
        nurbs = cmds.extrude(
            dummy_circle,
            curve_to_sweep,
            name=f"{posed_geometry.split('|')[-1]}_nurbs_01",
            ch=False,
            rn=False,
            po=0,
            et=2,
            ucp=1,
            fpt=1,
            upn=1,
            rotation=0,
            scale=1,
            rsp=1,
        )[0]
        cmds.setAttr(nurbs + ".overrideEnabled", 1)
        cmds.setAttr(nurbs + ".overrideVisibility", 0)
        cmds.parent(nurbs, curve_to_sweep)

    cmds.delete(dummy_circle)

    return (
        list_all_created_curve,
        list_perimeter_curve,
        list_pointer_curve_created,
    )


def dict_cord_master_uv_points_from_posed_mesh(
    posed_ref_geometry: str
) -> Dict[int, Tuple[float, float]]:
    """From a posed geometry return a dictionary with all the coordinates of the master UV points.

    Args:
        posed_ref_geometry (str): the posed ref version of the flatten geometry to bind.    

    Returns:
        dict_cord_master_uv_point (Dict[int,Tuple[float,float]]): the key is the index of the uv

    """
    list_component_on_uv_border = get_component_on_border(
        posed_ref_geometry, mode="UV")
    (
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    ) = (
        list_component_on_uv_border[2],
        list_component_on_uv_border[3],
        list_component_on_uv_border[0],
    )
    list_vertex_index_on_border = get_component_on_border(
        posed_ref_geometry, mode="vtx")
    dict_neighbor_uv_on_border = get_neighbors_uv_on_border(
        posed_ref_geometry,
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    )

    dict_cord_master_uv_point = get_cord_uv_master_point_posed_mesh(
        posed_ref_geometry,
        dict_neighbor_uv_on_border,
        list_vertex_index_on_border
    )
    return dict_cord_master_uv_point


def update_label(list_perimeter_curve: Union[str, Set[str]]) -> None:
    """Update the label indicator and color them. The "twin" indicator
    will be always updated. Do not do any bind just refresh the value.

    Args:
        list_perimeter_curve (Union[str, Set[str]]): the perimeter curve
        that are associated with the label indicator to update.
    """

    if isinstance(list_perimeter_curve, str):
        list_perimeter_curve = {list_perimeter_curve}
    list_shape_mesh_binded_to_label = set()
    dict_shape_mesh_and_set = {}
    dict_shape_mesh_and_label = {}
    dict_shape_mesh_and_perimeter_curve = {}
    dict_label_and_twin_label_curve = {}
    # this code works based on the fact that Maya will automatically assign the newly create
    # geometry inside the quick selection set. Just read the number the element that are in that set
    # an set that number to the appropriate indicator label. If the curve is binder change the color
    for perimeter_curve in list_perimeter_curve:
        if cmds.attributeQuery("connection_between_perimeter_and_pointer_curve",
                               node=perimeter_curve, exists=True):
            if not cmds.listConnections(
                    f"{perimeter_curve}.connection_between_label_and_perimeter_curve"):
                continue
            label_curve = (cmds.listConnections(
                f"{perimeter_curve}.connection_between_label_and_perimeter_curve").pop())
            if not cmds.listConnections(
                    f"{label_curve}.connection_between_quick_set_and_label_curve"):
                # if the set do not exist than skip
                continue
            edges_set = (cmds.listConnections(
                f"{label_curve}.connection_between_quick_set_and_label_curve").pop())
            element_in_set = flatten_selection_list(
                cmds.sets(edges_set, query=True))
            cmds.setAttr(label_curve +
                         '.connection_between_float_indicator_and_label_curve',
                         len(element_in_set))
            shape_mesh = cmds.listRelatives(
                cmds.ls(next(iter(element_in_set))), parent=True, type='mesh')[0]
            list_shape_mesh_binded_to_label.add(shape_mesh)

            if not shape_mesh in dict_shape_mesh_and_set:
                dict_shape_mesh_and_set[shape_mesh] = []
            dict_shape_mesh_and_set[shape_mesh].append(edges_set)
            if not shape_mesh in dict_shape_mesh_and_label:
                dict_shape_mesh_and_label[shape_mesh] = []
            dict_shape_mesh_and_label[shape_mesh].append(label_curve)
            if not shape_mesh in dict_shape_mesh_and_perimeter_curve:
                dict_shape_mesh_and_perimeter_curve[shape_mesh] = []
            dict_shape_mesh_and_perimeter_curve[shape_mesh].append(
                perimeter_curve)
        # if the curve is paired than update twin and set colors
        if (cmds.listConnections(
                f"{perimeter_curve}.connection_between_perimeter_and_pointer_curve")):

            pointer_curve = cmds.listConnections(
                f"{perimeter_curve}.connection_between_perimeter_and_pointer_curve").pop()
            twin_closest_curve = cmds.listConnections(
                f"{pointer_curve}.connection_between_perimeter_and_pointer_curve")
            twin_closest_curve.remove(perimeter_curve)
            twin_closest_curve = twin_closest_curve.pop()
            twin_label_curve = (cmds.listConnections(
                f"{twin_closest_curve}.connection_between_label_and_perimeter_curve").pop())

            if (label_curve, twin_label_curve) in dict_label_and_twin_label_curve:
                continue
            if not cmds.listConnections(
                    f"{twin_label_curve}.connection_between_quick_set_and_label_curve"):
                # if the user deletes a mesh that has been binded the set are deleted with the mesh.
                # the labels are not connected to a set has expected to be.
                continue

            twin_edges_set = (cmds.listConnections(
                f"{twin_label_curve}.connection_between_quick_set_and_label_curve").pop())
            element_in_twin_set = flatten_selection_list(
                cmds.sets(twin_edges_set, query=True))

            if len(element_in_set) == len(element_in_twin_set):
                cmds.setAttr(label_curve + '.overrideColor', 14)
                cmds.setAttr(twin_label_curve + '.overrideColor', 14)
            else:
                cmds.setAttr(label_curve + '.overrideColor', 13)
                cmds.setAttr(twin_label_curve + '.overrideColor', 13)
            dict_label_and_twin_label_curve[label_curve] = twin_label_curve
            dict_label_and_twin_label_curve[twin_label_curve] = label_curve

    for shape_mesh in dict_shape_mesh_and_set:
        history_node_shape_mesh = set(cmds.listHistory(shape_mesh))
        master_node = history_node_shape_mesh & dict_shape_mesh_and_set.keys()
        master_node -= {shape_mesh}
        if not master_node:
            continue

        master_node = master_node.pop()
        if master_node in list_shape_mesh_binded_to_label:
            # in this case the shape_mesh is the master and shape_mesh_twin is the slave.
            # Maya do not automatically update the set linked to a slave mesh.
            # - Identify the master and slave mesh.
            # - take a random edge for from the every set binded to the master mesh.
            # - By measuring the distance of the two vertices you know which is
            # the closest perimeter curve to that random edge.
            # - The topology is the same so find the closest perimeter curve to
            # the edge that as the same component id this times on the slave mesh.
            # - Doing so you know the pair of perimeter curves. The master
            # should give the label indicator its value to the slave one.

            set_connected_to_master_mesh = dict_shape_mesh_and_set.get(
                master_node)
            # print("master",master_node, "slave" ,shape_mesh,"set connected a slave")
            # print(dict_shape_mesh_and_set.get(shape_mesh), "master", set_connected_to_master_mesh)
            for set_master_mesh in set_connected_to_master_mesh:
                element_in_set = flatten_selection_list(
                    cmds.sets(set_master_mesh, query=True))
                random_edge_in_set = next(iter(element_in_set))
                list_two_vtx = flatten_selection_list(cmds.polyListComponentConversion(
                    random_edge_in_set, fromEdge=True, toVertex=True))
                list_two_vtx_pos = []
                for vertex in list_two_vtx:
                    list_two_vtx_pos.append(cmds.xform(
                        vertex, worldSpace=1, translation=1, query=1))
                id_random_edge_in_set_slave = get_index_component(
                    random_edge_in_set)
                random_edge_in_set_slave = f"{shape_mesh}.e[{str(id_random_edge_in_set_slave)}]"
                list_two_vtx_slave = flatten_selection_list(cmds.polyListComponentConversion(
                    random_edge_in_set_slave, fromEdge=True, toVertex=True))
                list_two_vtx_pos_slave = []
                for vertex in list_two_vtx_slave:
                    list_two_vtx_pos_slave.append(cmds.xform(
                        vertex, worldSpace=1, translation=1, query=1))
                perimeter_curve_binded_to_slave_mesh = dict_shape_mesh_and_perimeter_curve.get(
                    shape_mesh)

                dict_vertex_distance = {}
                for perimeter_curve in perimeter_curve_binded_to_slave_mesh:
                    selection_list = om2.MSelectionList()
                    selection_list.add(perimeter_curve)
                    mfn_curve = om2.MFnNurbsCurve(selection_list.getDagPath(0))
                    dict_vertex_distance[perimeter_curve] = 0
                    for vertex_pos in list_two_vtx_pos_slave:
                        vertex_position = om2.MPoint(vertex_pos)
                        distance_from_vertex = mfn_curve.distanceToPoint(
                            vertex_position, space=om2.MSpace.kWorld
                        )
                        dict_vertex_distance[perimeter_curve] += distance_from_vertex

                closest_curve_slave = None
                closest_distance = float("inf")
                for perimeter_curve, average_distance in dict_vertex_distance.items():
                    if average_distance < closest_distance:
                        closest_curve_slave = perimeter_curve
                        closest_distance = average_distance

                label_curve_slave = (cmds.listConnections(
                    f"{closest_curve_slave}.connection_between_label_and_perimeter_curve").pop())
                cmds.setAttr(label_curve_slave +
                             '.connection_between_float_indicator_and_label_curve',
                             len(element_in_set))
                label_curve_twin = dict_label_and_twin_label_curve.get(
                    label_curve_slave)
                if label_curve_twin is not None:
                    var_twin = cmds.getAttr(label_curve_twin +
                                            '.connection_between_float_indicator_and_label_curve')
                    if len(element_in_set) == var_twin:
                        cmds.setAttr(label_curve_slave + '.overrideColor', 14)
                        cmds.setAttr(label_curve_twin + '.overrideColor', 14)
                    else:
                        cmds.setAttr(label_curve_slave + '.overrideColor', 13)
                        cmds.setAttr(label_curve_twin + '.overrideColor', 13)


def bind_label_indicator(
    list_input_geometry: Union[str, List[str], Set[str]],
    bool_create_label: bool,
    bool_unhide_updated_label: bool,
    bool_just_return_found_label_curve: bool = False,
    bool_check_if_proper_input: bool = True,
    posed_ref_geometry: str = None,
    dict_cord_master_uv_point: Dict[int, Tuple[float, float]] = None,
    list_perimeter_curve: Set[str] = None,
) -> Set[str]:
    """Bind the smart label indicator to a given geometry.

    Args:
        list_input_geometry (Union[List[str], Set[str]]): the geometry to bind.
        bool_create_label (bool): the label indicator already exists or not.
        bool_unhide_updated_label (bool): unhide the just binded label curve.
        bool_just_return_found_label_curve (bool): just return the label curve to bind.
        bool_check_if_proper_input (bool): check if the input meshes are ok or not.
        posed_ref_geometry (str): the posed ref version of the flatten geometry to bind.    
        dict_cord_master_uv_point (Dict[int, Tuple[float, float]]): the key is the index of the uv
        master point found. The value is the corresponding coordinate of that point.
        list_perimeter_curve (Set[str]): the curve that live on the perimeter of the unwrapped
        geometry to bind.

    Returns:
        Set[str]: the list of labels curves that has been binded.
    """
    # the uv master point are mandatory
    if dict_cord_master_uv_point is None and posed_ref_geometry is None:
        raise TypeError(
            "Input a dictionary with the master uv points or the posed ref geometry")

    # if you are dealing with a user type of input it is better to have it on
    if bool_check_if_proper_input:
        if isinstance(list_input_geometry, str):
            list_input_geometry = {list_input_geometry}

        for mesh in list_input_geometry:
            raise_error_if_mesh_has_missing_uvs(mesh)
            raise_error_if_mesh_has_overlapping_uvs(mesh)
            raise_error_if_mesh_is_unflat(mesh)
        if posed_ref_geometry:
            cmds.delete(posed_ref_geometry, constructionHistory=True)
            cmds.makeIdentity(posed_ref_geometry, apply=True,
                              translate=True, rotate=True, scale=True, preserveNormals=True)
            raise_error_if_mesh_has_missing_uvs(posed_ref_geometry)
            raise_error_if_mesh_has_one_uv_shell(posed_ref_geometry)
            raise_error_if_mesh_has_overlapping_uvs(posed_ref_geometry)
            raise_error_if_mesh_has_unpairable_uv_border(posed_ref_geometry)
    # if both are input choose the already done dict_cord_master_uv_point
    if posed_ref_geometry is not None and dict_cord_master_uv_point is None:
        dict_cord_master_uv_point = dict_cord_master_uv_points_from_posed_mesh(
            posed_ref_geometry)

    if list_perimeter_curve is None:
        list_perimeter_curve = set()
        for curve in return_curve_in_scene()[0]:
            if cmds.attributeQuery("connection_between_perimeter_and_pointer_curve",
                                   node=curve, exists=True):
                if cmds.attributeQuery("connection_between_label_and_perimeter_curve",
                                       node=curve, exists=True):
                    list_perimeter_curve.add(curve)

    list_closest_curve = set()
    list_label_curve = set()
    for shell in list_input_geometry:
        if bool_create_label:
            list_input_index_master_uv_point = re_find_uv_master_point(
                dict_cord_master_uv_point, dict_uv_cord_to_compare_to(shell))
        else:
            list_input_index_master_uv_point = re_find_uv_master_point(
                dict_cord_master_uv_point, dict_uv_cord_to_compare_to(shell), 0.0001)

        list_edge_loop_path = create_curve(
            shell,
            list_input_index_master_uv_point,
            just_return_list_edge_loop_full_name=True
        )

        for edge_loop_path in list_edge_loop_path:
            list_vertex_loop_path = cmds.polyListComponentConversion(
                edge_loop_path, fromEdge=True, toVertex=True)
            list_vertex_loop_path = flatten_selection_list(
                list_vertex_loop_path)
            list_curve_pos = []
            for vertex in list_vertex_loop_path:
                list_curve_pos.append(cmds.xform(
                    vertex, worldSpace=1, translation=1, query=1))

            closest_curve = None
            closest_distance = float("inf")
            dict_vertex_distance = {}
            for perimeter_curve in list_perimeter_curve:
                selection_list = om2.MSelectionList()
                selection_list.add(perimeter_curve)
                mfn_curve = om2.MFnNurbsCurve(selection_list.getDagPath(0))
                dict_vertex_distance[perimeter_curve] = 0
                for vertex_position in list_curve_pos:
                    vertex_position = om2.MPoint(vertex_position)
                    distance_from_vertex = mfn_curve.distanceToPoint(
                        vertex_position, space=om2.MSpace.kWorld
                    )
                    dict_vertex_distance[perimeter_curve] += distance_from_vertex

                dict_vertex_distance[perimeter_curve] /= len(
                    list_vertex_loop_path)

            closest_curve = None
            closest_distance = float("inf")
            for perimeter_curve, average_distance in dict_vertex_distance.items():
                if average_distance < closest_distance:
                    closest_curve = perimeter_curve
                    closest_distance = average_distance
            list_closest_curve.add(closest_curve)

            if bool_just_return_found_label_curve:
                label_curve = cmds.listConnections(
                    f"{closest_curve}.connection_between_label_and_perimeter_curve").pop()
                list_label_curve.add(label_curve)
                continue

            if bool_create_label:
                if cmds.getAttr(f"{closest_curve}.form") == 0:  # open curve
                    name_dummy_curve = cmds.duplicate(
                        closest_curve, name="dummy_closest_curve")[0]
                    cmds.rebuildCurve(name_dummy_curve, ch=0, rpo=1, rt=0, end=1, kr=0, kcp=0,
                                      kep=0, kt=0, s=2, d=1, fr=0, tol=0)
                    pos_center_curve = cmds.xform(f"{name_dummy_curve}.ep[1]",
                                                  worldSpace=True, translation=True, q=True)
                    cmds.delete(name_dummy_curve)
                else:
                    pos_center_curve = cmds.xform(f"{closest_curve}.ep[0]",
                                                  worldSpace=True, translation=True, q=True)
                label_curve = cmds.circle(center=pos_center_curve,
                                          radius=0.0000001,
                                          sections=2,
                                          constructionHistory=False,
                                          name="label_00")[0]
                label_indicator = cmds.paramDimension(label_curve + '.u[0.5]')
                edges_set = cmds.sets(
                    name="set_label_0001", empty=True, edges=True)
                cmds.sets(edge_loop_path, add=edges_set)
                element_in_set = flatten_selection_list(
                    cmds.sets(edges_set, query=True))
                cmds.addAttr(
                    [label_indicator, label_curve],
                    longName="connection_between_indicator_and_label_curve",
                    attributeType="long",
                    min=0,
                    max=0,
                    defaultValue=0,
                    hidden=True,
                )
                cmds.addAttr(
                    [label_curve, closest_curve],
                    longName="connection_between_label_and_perimeter_curve",
                    attributeType="long",
                    min=0,
                    max=0,
                    defaultValue=0,
                    hidden=True,
                )
                cmds.addAttr(
                    [label_curve, edges_set],
                    longName="connection_between_quick_set_and_label_curve",
                    attributeType="long",
                    min=0,
                    max=0,
                    defaultValue=0,
                    hidden=True,
                )
                cmds.addAttr(
                    label_curve,
                    longName="connection_between_float_indicator_and_label_curve",
                    attributeType="long",
                    hidden=True,
                )
                cmds.connectAttr(
                    f"{label_indicator}.connection_between_indicator_and_label_curve",
                    f"{label_curve}.connection_between_indicator_and_label_curve",
                )
                cmds.connectAttr(
                    f"{label_curve}.connection_between_label_and_perimeter_curve",
                    f"{closest_curve}.connection_between_label_and_perimeter_curve",
                )
                cmds.connectAttr(
                    f"{label_curve}.connection_between_quick_set_and_label_curve",
                    f"{edges_set}.connection_between_quick_set_and_label_curve",
                )
                cmds.connectAttr(label_curve +
                                 '.connection_between_float_indicator_and_label_curve',
                                 label_indicator + '.uParamValue')
                cmds.setAttr(label_curve +
                             '.connection_between_float_indicator_and_label_curve',
                             len(element_in_set))
                cmds.setAttr(label_curve + '.overrideEnabled', 1)
                cmds.setAttr(label_curve + '.overrideColor', 1)
                cmds.setAttr(f"{label_curve}.visibility", 0)
                list_label_curve.add(label_curve)

            else:

                label_curve = (cmds.listConnections(
                    f"{closest_curve}.connection_between_label_and_perimeter_curve").pop())
                previous_quick_set = cmds.listConnections(
                    f"{label_curve}.connection_between_quick_set_and_label_curve")
                if previous_quick_set:
                    deleted_nodes = [node for node in  # pylint: disable=unused-variable
                                     previous_quick_set if cmds.delete(node)]
                edges_set = cmds.sets(
                    name="set_label_0001", empty=True, edges=True)  # edges=1 make them invisible
                cmds.sets(edge_loop_path, add=edges_set)
                element_in_set = flatten_selection_list(
                    cmds.sets(edges_set, query=True))

                cmds.addAttr(
                    edges_set,
                    longName="connection_between_quick_set_and_label_curve",
                    attributeType="long",
                    min=0,
                    max=0,
                    defaultValue=0,
                    hidden=True,
                )
                cmds.connectAttr(
                    f"{label_curve}.connection_between_quick_set_and_label_curve",
                    f"{edges_set}.connection_between_quick_set_and_label_curve",
                )
                cmds.setAttr(label_curve +
                             '.connection_between_float_indicator_and_label_curve',
                             len(element_in_set))
                list_label_curve.add(label_curve)

    if not bool_just_return_found_label_curve:
        update_label(list_closest_curve)

        if bool_unhide_updated_label:
            for label in list_label_curve:
                cmds.setAttr(label + '.visibility', 1)

    return list_label_curve


def analyze_and_unwrap(
    geometry_name: str
) -> Tuple[str, List[str], List[str], List[str], List[str], List[str], List[str]]:
    """Given a geometry name, flatten the geometry and create the curve pairings.

    Args:
        geometry_name (str): The name of the geometry to process.

    Returns:
        Tuple[str, List[str], List[str], List[str], List[str]]: contains
        name unwrapped ref geometry, list of shell unwrapped ref geometry,
        list perimeter curves, list pointer curves, list label curves,
        list all curves, list all folders
    """

    # Construction history optional to delete but it may slow down calculation.
    cmds.delete(geometry_name, constructionHistory=True)
    cmds.makeIdentity(geometry_name, apply=True,
                      translate=True, rotate=True, scale=True, preserveNormals=True)
    raise_error_if_mesh_has_missing_uvs(geometry_name)
    raise_error_if_mesh_has_one_uv_shell(geometry_name)
    raise_error_if_mesh_has_overlapping_uvs(geometry_name)
    raise_error_if_mesh_has_unpairable_uv_border(geometry_name)

    # get all the require information about the components of the mesh
    list_component_on_uv_border = get_component_on_border(
        geometry_name, mode="UV")
    (
        list_vertex_on_uv_border_index,
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    ) = (
        list_component_on_uv_border[1],
        list_component_on_uv_border[2],
        list_component_on_uv_border[3],
        list_component_on_uv_border[0],
    )
    list_vertex_index_on_border = get_component_on_border(
        geometry_name, mode="vtx")
    dict_neighbor_uv_on_border = get_neighbors_uv_on_border(
        geometry_name,
        list_edge_on_uv_border_index,
        list_face_on_uv_border_index,
        list_uv_on_border_index,
    )

    # find the all the UV master point of the "posed_geometry_name" and store their coordinate
    dict_cord_master_uv_point = get_cord_uv_master_point_posed_mesh(
        geometry_name, dict_neighbor_uv_on_border, list_vertex_index_on_border
    )

    # unwrap the posed GEO and separate it in multiple shell
    unwrapped_geo = unwrap(geometry_name, list_vertex_on_uv_border_index)

    if cmds.polyEvaluate(geometry_name, uv=True) != cmds.polyEvaluate(
        unwrapped_geo, uv=True
    ):
        cmds.delete(unwrapped_geo)
        message(
            f"Unable to unwrap. Try to make more room between the uv shell of : {geometry_name}",
            raise_error=True,
        )

    shell_group_folder = cmds.duplicate(
        unwrapped_geo, name=f"{geometry_name.split('|')[-1]}_separated_shells"
    )[0]
    list_input_geometry = cmds.polySeparate(
        shell_group_folder, name=f"{geometry_name.split('|')[-1]}_shell_01",
        constructionHistory=False)  # polySeparate create a folder

    #
    list_input_index_master_uv_point = re_find_uv_master_point(
        dict_cord_master_uv_point, dict_uv_cord_to_compare_to(unwrapped_geo)
    )

    # for each flatten shell calculate the list of curve using the Master UV point
    dummy_attr_perimeter_pointer_curve = (
        "connection_between_unwrapped_geo_and_separated_shells"
    )
    cmds.addAttr(
        [unwrapped_geo] + list_input_geometry,
        longName=dummy_attr_perimeter_pointer_curve,
        attributeType="long",
        min=0,
        max=0,
        defaultValue=0,
        hidden=True,
    )

    list_all_created_curve = set()
    for shell in list_input_geometry:
        list_input_index_master_uv_point = re_find_uv_master_point(
            dict_cord_master_uv_point, dict_uv_cord_to_compare_to(shell)
        )

        list_created_curve = create_curve(
            shell,
            list_input_index_master_uv_point,
            just_return_list_edge_loop_full_name=False
        )
        list_all_created_curve = list_all_created_curve.union(
            list_created_curve)
        cmds.connectAttr(
            f"{unwrapped_geo}.{dummy_attr_perimeter_pointer_curve}",
            f"{shell}.{dummy_attr_perimeter_pointer_curve}",
        )
    # Pair the curve in couple. The curves with a pair will be black
    (
        list_all_created_curve,
        list_all_perimeter_curve,
        list_all_pointer_curve,
    ) = bind_curve(
        geometry_name, unwrapped_geo, list_all_created_curve, list_uv_on_border_index
    )
    list_label_curve = bind_label_indicator(
        list_input_geometry,
        bool_create_label=True,
        bool_unhide_updated_label=False,
        bool_just_return_found_label_curve=False,
        bool_check_if_proper_input=False,
        posed_ref_geometry=None,
        dict_cord_master_uv_point=dict_cord_master_uv_point,
        list_perimeter_curve=list_all_perimeter_curve
    )
    # do some organization
    cmds.hide([geometry_name, unwrapped_geo])
    cmds.setAttr(shell_group_folder + ".visibility", 1)

    perimeter_curve_folder = cmds.group(
        empty=True, name=f"{geometry_name.split('|')[-1]}_perimeter")
    pointer_curve_folder = cmds.group(
        empty=True, name=f"{geometry_name.split('|')[-1]}_pointer")
    label_curve_folder = cmds.group(
        empty=True, name=f"{geometry_name.split('|')[-1]}_label")
    curve_folder = cmds.group(
        empty=True, name=f"{geometry_name.split('|')[-1]}_curve")
    master_folder = cmds.group(
        empty=True, name=f"{geometry_name.split('|')[-1]}_unwrap_analyze_output")

    unwrapped_geo = cmds.parent(unwrapped_geo, master_folder)[0]
    shell_group_folder = cmds.parent(shell_group_folder, master_folder)
    curve_folder = cmds.parent(curve_folder, master_folder)
    perimeter_curve_folder = cmds.parent(perimeter_curve_folder, curve_folder)
    pointer_curve_folder = cmds.parent(pointer_curve_folder, curve_folder)
    label_curve_folder = cmds.parent(label_curve_folder, curve_folder)
    list_all_perimeter_curve = cmds.parent(
        list_all_perimeter_curve, perimeter_curve_folder)
    list_all_pointer_curve = cmds.parent(
        list_all_pointer_curve, pointer_curve_folder)
    list_label_curve = cmds.parent(
        list_label_curve, label_curve_folder)
    list_all_created_curve = (
        list_all_perimeter_curve + list_all_pointer_curve + list_label_curve)
    cmds.move(0, 0, 0.001, curve_folder, absolute=True)  # better for hitest()
    list_folders = [master_folder, shell_group_folder,
                    curve_folder, perimeter_curve_folder, pointer_curve_folder, label_curve_folder]
    cmds.select(clear=True)

    global last_unwrapped_ref_geo_created  # pylint: disable=global-variable-undefined
    last_unwrapped_ref_geo_created = unwrapped_geo
    global last_posed_ref_geo_created  # pylint: disable=global-variable-undefined
    last_posed_ref_geo_created = geometry_name

    return (unwrapped_geo, list_input_geometry, list_all_perimeter_curve,
            list_all_pointer_curve, list_label_curve, list_all_created_curve, list_folders)


def calculate_faces_per_material(geometry_name: str) -> Dict[str, Set[str]]:
    """Return the faces associated with each material assigned to the geometry.

    Args:
        geometry_name (str): the name of the geometry.

    Returns:
        Dict[str,Set[str]]: The key is the material name and the value is the faces that are
        associated with it and that also live on the input geometry.
    """

    if cmds.polyEvaluate(geometry_name, uv=True) != 0:
        cmds.polyMapDel(geometry_name + ".map[*]", constructionHistory=False)

    shape_node = cmds.listRelatives(
        geometry_name, shapes=True, path=True)[0]
    list_shading_engines = cmds.listConnections(
        shape_node, type="shadingEngine")
    if list_shading_engines is None:
        return None

    dict_faces_per_material = {}
    for shading_engine in set(list_shading_engines):
        every_surface_where_assigned = cmds.sets(shading_engine, q=True)
        if every_surface_where_assigned is None:
            continue
        list_face_filtered = set()
        for component in every_surface_where_assigned:
            split_component_part = component.split(".")
            if len(split_component_part) == 1:
                list_face_filtered.update(
                    cmds.ls(f"{component}.f[*]", flatten=True))
            else:
                if split_component_part[0] == geometry_name:
                    list_face_filtered.update(
                        flatten_selection_list(component))

        dict_faces_per_material[shading_engine] = list_face_filtered

    return dict_faces_per_material


def are_two_meshes_identical(
    geometry_00: Union[str, om2.MPointArray],
    geometry_01: Union[str, om2.MPointArray],
) -> bool:
    """Check if the two given geometry are identical.

    Args:
        geometry_00 (Union[str,om2.MPointArray]): the name of the first geometry or the MPointArray
        with all the vertex positions.
        geometry_01 (Union[str,om2.MPointArray]): the name of the second geometry or the MPointArray
        with all the vertex positions.
    Returns:
        bool: Returns true if the geometry are identical. Otherwise returns false.
    """
    if isinstance(geometry_00, str):
        selection_list = om2.MSelectionList()
        selection_list.add(geometry_00)
        mfn_mesh_00 = om2.MFnMesh(selection_list.getDagPath(0))
        list_cord_vtx_mesh_00 = mfn_mesh_00.getPoints()
    else:
        list_cord_vtx_mesh_00 = geometry_00

    if isinstance(geometry_01, str):
        selection_list = om2.MSelectionList()
        selection_list.add(geometry_01)
        mfn_mesh_01 = om2.MFnMesh(selection_list.getDagPath(0))
        list_cord_vtx_mesh_01 = mfn_mesh_01.getPoints()
    else:
        list_cord_vtx_mesh_01 = geometry_01

    threshold = 0.000001

    if len(list_cord_vtx_mesh_00) != len(list_cord_vtx_mesh_01):
        return False

    for (cord_point_00, cord_point_01) in zip(list_cord_vtx_mesh_00, list_cord_vtx_mesh_01):
        if not cord_point_00.isEquivalent(cord_point_01, threshold):
            return False
    return True


def create_material(
    material_name: str,
    type_material: str = "lambert"
) -> Tuple[str, str]:
    """Create a material with a shading group attached.

    Args:
        name (str): the name of the material to create.
        type_material (str, optional): The type of the material to create. Defaults to lambert.

    Returns:
        Tuple[str,str]: the first str is the name of the material created and the second one
        is the name of the shading group that is linked to the material. 
    """
    material_name = cmds.shadingNode(
        type_material, name=material_name, asShader=True)
    shader_name = cmds.sets(name=f"{material_name.split('|')[-1]}_SHA", empty=True,
                            renderable=True, noSurfaceShader=True)
    cmds.connectAttr(f"{material_name}.outColor",
                     f"{shader_name}.surfaceShader")
    return material_name, shader_name


def ordered_vertex_loop_from_edge_loop(
    edge_loop_path: List[str]
) -> List[str]:
    """Get the vertex in topological order from a given edge loop selection.

    Args:
        edge_loop_path (List[str]): the full name of the edges to order.

    Returns:
        List[str]: all the vertexes that make the input edge loop path listed in topological order. 
    """
    # TODO: rewrite this
    shape_node = cmds.listRelatives(
        edge_loop_path[0], path=True, parent=True)
    transform_node = cmds.listRelatives(
        shape_node[0], path=True, parent=True)
    edge_number_list = []
    for edge in edge_loop_path:
        check_number = ((edge.split('.')[1]).split('\n')[0]).split(' ')
        for check in check_number:
            find_number = ''.join(
                [n for n in check.split('|')[-1] if n.isdigit()])
            if find_number:
                edge_number_list.append(find_number)
    get_number = []
    for edge in edge_loop_path:
        vtx_list = cmds.polyInfo(edge, edgeToVertex=True)
        check_number = ((vtx_list[0].split(':')[1]).split('\n')[0]).split(' ')
        for check in check_number:
            find_number = ''.join(
                [n for n in check.split('|')[-1] if n.isdigit()])
            if find_number:
                get_number.append(find_number)
    dup = set([x for x in get_number if get_number.count(x) > 1])
    get_head_tail = list(set(get_number) - dup)
    check_circle_state = 0
    if not get_head_tail:  # close curve
        check_circle_state = 1
        get_head_tail.append(get_number[0])
    vft_order = []
    vft_order.append(get_head_tail[0])
    count = 0
    while len(dup) > 0 and count < 1000:
        check_vtx = transform_node[0]+'.vtx[' + vft_order[-1] + ']'
        vtx_list = cmds.polyInfo(check_vtx, ve=True)
        get_number = []
        check_number = ((vtx_list[0].split(':')[1]).split('\n')[0]).split(' ')
        for check in check_number:
            find_number = ''.join(
                [n for n in check.split('|')[-1] if n.isdigit()])
            if find_number:
                get_number.append(find_number)
        find_next_edge = []
        for get in get_number:
            if get in edge_number_list:
                find_next_edge = get
        edge_number_list.remove(find_next_edge)
        check_vtx = transform_node[0]+'.e[' + find_next_edge + ']'
        find_vtx = cmds.polyInfo(check_vtx, ev=True)
        get_number = []
        check_number = ((find_vtx[0].split(':')[1]).split('\n')[0]).split(' ')
        for check in check_number:
            find_number = ''.join(
                [n for n in check.split('|')[-1] if n.isdigit()])
            if find_number:
                get_number.append(find_number)
        got_next_vtx = []
        for get in get_number:
            if get in dup:
                got_next_vtx = get
        dup.remove(got_next_vtx)
        vft_order.append(got_next_vtx)
        count += 1
    if check_circle_state == 0:
        vft_order.append(get_head_tail[1])
    else:  # close curve remove connected vtx
        if vft_order[0] == vft_order[1]:
            vft_order = vft_order[1:]
        elif vft_order[0] == vft_order[-1]:
            vft_order = vft_order[0:-1]
    final_list = []
    for vertex in vft_order:
        final_list.append(transform_node[0]+'.vtx[' + vertex + ']')
    return final_list


def get_smart_mirror_twin(geometry_name: str) -> Optional[Union[str, Set[str]]]:
    """Get the name of the mirror mesh by checking the outMesh and inMesh connections

    Args:
        geometry_name (str): the name of the geometry to check

    Returns:
        Optional[Union[str,Set[str]]][str]: a if Set the geometry has multiple "slaves" mirror
    """

    # A mesh can have multiple "slave" mirror. All the modifications applied to
    # the mesh that output "outMesh" connection are mirrored to all the meshes
    # that that take that connection within "inMesh".
    # All the edit done to the "slave" do not propagate to the "master".
    # If you delete the history of the slave the connection is lost.

    if cmds.listConnections(f'{geometry_name}.outMesh'):
        if cmds.listConnections(f'{geometry_name}.inMesh'):
            # if the mesh has history it can happen to have both connected outMesh and inMesh
            cmds.delete(geometry_name, constructionHistory=True)

    if cmds.listConnections(f'{geometry_name}.outMesh'):
        if cmds.listConnections(f'{geometry_name}.inMesh'):
            raise RuntimeError("Both 'outMesh' and 'inMesh'")

    if cmds.listConnections(f'{geometry_name}.outMesh'):
        list_nodes_forewords = cmds.listHistory(geometry_name, future=True)
        list_shape_nodes_connected = set()
        for node in list_nodes_forewords:
            if cmds.objectType(node, isType="mesh"):
                if cmds.listConnections(f'{node}.inMesh'):
                    if cmds.ls(node, noIntermediate=True):
                        list_shape_nodes_connected.add(node)
        return list_shape_nodes_connected

    if cmds.listConnections(f'{geometry_name}.inMesh'):
        list_nodes_backwards = cmds.listHistory(geometry_name)
        list_shape_nodes_connected = set()
        for node in list_nodes_backwards:
            if cmds.objectType(node, isType="mesh"):
                if cmds.listConnections(f'{node}.outMesh'):
                    list_shape_nodes_connected.add(node)
        return list_shape_nodes_connected.pop()


def run_relax_sculpt_mode(
        list_geo_to_relax: Union[str, List[str], Set[str]],
        relax_till_done: bool):
    """Relax the input mesh using the relax inside the sculpt mode.

    Args:
        list_geo_to_relax (Union[str,List[str],Set[str]]): the mesh to relax.
        relax_till_done (bool): relax till the mesh is completely evenly distributed.
    """
    # the sculpt mode seams to effect only the first mesh island
    if isinstance(list_geo_to_relax, str):
        list_geo_to_relax = {list_geo_to_relax}

    for mesh in list_geo_to_relax:
        if cmds.polyEvaluate(mesh, shell=True) != 1:
            message(f"The mesh: {mesh} appears to have multiple shell. The sculpt mode do not work",
                    raise_error=True)

    # disable the highlighting. Optional but better in sculpting mode.
    is_selection_highlight_enabled = {}
    for panel in cmds.getPanel(type="modelPanel"):
        cmds.modelEditor(panel, query=True, selectionHiliteDisplay=True)
        is_selection_highlight_enabled[panel] = cmds.modelEditor(panel, query=True,
                                                                 selectionHiliteDisplay=True)
        cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=False)

    if relax_till_done:
        main_progress_bar = mel.eval('$tmp = $gMainProgressBar')
        cmds.progressBar(main_progress_bar,
                         edit=True,
                         beginProgress=True,
                         isInterruptable=True,
                         status='Relaxing till even ...',
                         )
        dict_mesh_and_mfn_mesh = {}
        selection_list = om2.MSelectionList()
        for counter, mesh in enumerate(list_geo_to_relax):
            selection_list.add(mesh)
            dict_mesh_and_mfn_mesh[mesh] = om2.MFnMesh(
                selection_list.getDagPath(counter))

    # relax the inner vertexes
    mesh_to_select = set(f"{mesh}.vtx[*]" for mesh in list_geo_to_relax)
    cmds.select(mesh_to_select)
    cmds.polySelectConstraint(type=0x0001, mode=3, where=1)
    vertexes_inside_to_pin_full_name = cmds.ls(selection=True)
    cmds.polySelectConstraint(mode=0, where=0)
    sculpt_context_relax_flatten = "sculpt_context_relax_flatten_garment"
    if not cmds.sculptMeshCacheCtx(sculpt_context_relax_flatten, exists=True):
        cmds.sculptMeshCacheCtx(sculpt_context_relax_flatten)
    cmds.setToolTo(sculpt_context_relax_flatten)
    cmds.SetMeshFreezeTool()
    cmds.SculptMeshUnfreezeAll()
    if cmds.currentCtx() != "selectSuperContext":
        cmds.setToolTo('selectSuperContext')
    cmds.select(vertexes_inside_to_pin_full_name)
    cmds.sculptMeshCacheCtx(
        "sculptMeshCacheContext", edit=True, freezeSelection=100)
    cmds.SetMeshRelaxTool()
    cmds.sculptMeshCacheCtx("sculptMeshCacheContext", edit=True,
                            wireframeColor=[0, 0, 0], wireframeAlpha=1)
    stop_relax = False
    while stop_relax is not True:
        if relax_till_done:
            if cmds.progressBar(main_progress_bar, query=True, isCancelled=True):
                stop_relax = True
                cmds.progressBar(main_progress_bar,
                                 edit=True, endProgress=True)
            dict_points_position_before_relax = {}
            for mesh, mfn_mesh in dict_mesh_and_mfn_mesh.items():
                dict_points_position_before_relax[mesh] = mfn_mesh.getPoints()
        else:
            stop_relax = True
        for i in range(0, 100):  # pylint: disable=unused-variable
            cmds.sculptMeshCacheCtx(
                "sculptMeshCacheContext", edit=True, flood=100)

        if relax_till_done:
            dict_points_position_after_relax = {}
            for mesh, mfn_mesh in dict_mesh_and_mfn_mesh.items():
                dict_points_position_after_relax[mesh] = mfn_mesh.getPoints()
            for mesh in list_geo_to_relax:
                point_before_relax = dict_points_position_before_relax.get(
                    mesh)
                point_after_relax = dict_points_position_after_relax.get(mesh)
                # Check if the two point arrays are equal
                for (point_before, point_after) in zip(point_before_relax, point_after_relax):
                    if not point_before.isEquivalent(point_after, 0.000001):
                        break
                else:
                    stop_relax = True
                    cmds.progressBar(main_progress_bar,
                                     edit=True, endProgress=True)

    cmds.SetMeshFreezeTool()
    cmds.SculptMeshUnfreezeAll()
    if cmds.currentCtx() != "selectSuperContext":
        cmds.setToolTo('selectSuperContext')

    for panel, is_selection_highlight_enabled in is_selection_highlight_enabled.items():
        if is_selection_highlight_enabled:
            cmds.modelEditor(panel, edit=True, selectionHiliteDisplay=True)


def relax_flat_mesh(
    list_geo_to_relax: Union[str, Union[List[str], Set[str]]],
    posed_ref_geometry: str,
    flatten_ref_geometry: str,
) -> None:
    """Relax the input flatten geometry. Requires the posed and flatten ref geometry.

    Args:
        list_geo_to_relax (Union[str, Union[List[str], Set[str]]]): the geometry to relax.
        posed_ref_geometry (str): the posed ref version of the flatten geometry to relax.
        flatten_ref_geometry (str): the flatten ref version of the flatten geometry to relax.
    """
    # The flatten geometry to relax get the UV from the flattened ref geometry.
    # the UV master points are re-found on the input mesh to relax.
    # By analyzing the UV master points all the edge loops paths are found.
    # Compare every perimeter curve to find the closest to the current edge loop path.
    # When found give the curve as many cv points as the vertex of the edge loop path.
    # Move the vertexes in topological order to the just rebuild curve.
    # When the perimeter of the mesh is relax then use the relax brush
    # context for relax inner vertexes.
    if isinstance(list_geo_to_relax, str):
        list_geo_to_relax = {list_geo_to_relax}
    else:
        list_geo_to_relax = set(list_geo_to_relax)

    dict_patent_and_child_smart_mirror = {}
    for geo_to_relax in list_geo_to_relax.copy():
        if cmds.listConnections(f'{geo_to_relax}.outMesh'):
            list_slave_mirror_node = get_smart_mirror_twin(geo_to_relax)
            dict_patent_and_child_smart_mirror[geo_to_relax] = list_slave_mirror_node
            # if mesh is parent smart mirror than relax also the children
            if isinstance(list_slave_mirror_node, set):
                for slave in list_slave_mirror_node:
                    name_slave_mesh = cmds.listRelatives(
                        slave,
                        parent=True,
                        type='transform')[0]
                    list_geo_to_relax.add(name_slave_mesh)
            if isinstance(list_slave_mirror_node, str):
                name_slave_mesh = cmds.listRelatives(
                    list_slave_mirror_node,
                    parent=True,
                    type='transform')[0]
                list_geo_to_relax.add(name_slave_mesh)
        if cmds.listConnections(f'{geo_to_relax}.inMesh'):
            parent_mesh = get_smart_mirror_twin(geo_to_relax)
            if not parent_mesh in dict_patent_and_child_smart_mirror:
                dict_patent_and_child_smart_mirror[parent_mesh] = set()
            dict_patent_and_child_smart_mirror[parent_mesh].add(geo_to_relax)

    for geo_to_relax in list_geo_to_relax:
        raise_error_if_mesh_is_unflat(geo_to_relax)

    for geo_to_relax in list_geo_to_relax:
        cmds.transferAttributes(flatten_ref_geometry, geo_to_relax,
                                transferPositions=0, transferNormals=0, transferUVs=2,
                                transferColors=2, sampleSpace=0, sourceUvSpace="map1",
                                targetUvSpace="map1", searchMethod=3, flipUVs=0,
                                colorBorders=1)
        cmds.setAttr(geo_to_relax + '.displayColors', 0)
        raise_error_if_mesh_has_missing_uvs(geo_to_relax)
        raise_error_if_mesh_has_overlapping_uvs(geo_to_relax)
    for geo_to_relax in list_geo_to_relax:
        cmds.delete(geo_to_relax, constructionHistory=True)

    # find the all the UV master point of the "posed_ref_geometry_name" and store their coordinate
    dict_cord_master_uv_point = dict_cord_master_uv_points_from_posed_mesh(
        posed_ref_geometry)

    list_perimeter_curve = set()
    for curve in return_curve_in_scene()[0]:
        if cmds.attributeQuery("connection_between_perimeter_and_pointer_curve",
                               node=curve, exists=True):
            if cmds.attributeQuery("connection_between_label_and_perimeter_curve",
                                   node=curve, exists=True):
                list_perimeter_curve.add(curve)

    for curve in list_perimeter_curve:
        cmds.move(0, 0, -0.001, curve, absolute=True)  # better for hitest()
    list_mesh_to_relax_precisely = set()  # to relax until they are perfectly spaced
    for geo_to_relax in list_geo_to_relax:

        list_input_index_master_uv_point = re_find_uv_master_point(
            dict_cord_master_uv_point, dict_uv_cord_to_compare_to(geo_to_relax), 0.0001)
        list_edge_loop_path = create_curve(
            geo_to_relax,
            list_input_index_master_uv_point,
            just_return_list_edge_loop_full_name=True
        )

        for edge_loop_path in list_edge_loop_path:
            ordered_vertex_loop_path = ordered_vertex_loop_from_edge_loop(
                list(edge_loop_path))

            list_curve_pos = []
            for vertex in ordered_vertex_loop_path:
                list_curve_pos.append(cmds.xform(
                    vertex, worldSpace=1, translation=1, query=1))

            closest_curve = None
            closest_distance = float("inf")
            dict_vertex_distance = {}
            for perimeter_curve in list_perimeter_curve:
                selection_list = om2.MSelectionList()
                selection_list.add(perimeter_curve)
                mfn_curve = om2.MFnNurbsCurve(selection_list.getDagPath(0))
                dict_vertex_distance[perimeter_curve] = 0
                for vertex_position in list_curve_pos:
                    vertex_position = om2.MPoint(vertex_position)
                    distance_from_vertex = mfn_curve.distanceToPoint(
                        vertex_position, space=om2.MSpace.kWorld)
                    dict_vertex_distance[perimeter_curve] += distance_from_vertex
                dict_vertex_distance[perimeter_curve] /= len(
                    ordered_vertex_loop_path)

            closest_curve = None
            closest_distance = float("inf")
            for perimeter_curve, average_distance in dict_vertex_distance.items():
                if average_distance < closest_distance:
                    closest_curve = perimeter_curve
                    closest_distance = average_distance
            # Check if reversing the curve is needed.
            # It the vertex would travel less in 3D space the curve is reversed.
            distance_first_direction = 0
            distance_other_direction = 0
            counter_reversed = list(range(len(ordered_vertex_loop_path)))[::-1]
            if cmds.getAttr(f"{closest_curve}.form") == 0:
                len_ratio = len(list_curve_pos)-1
            else:
                len_ratio = len(list_curve_pos)
                list_mesh_to_relax_precisely.add(geo_to_relax)
            for counter, vertex in enumerate(ordered_vertex_loop_path):
                ratio_pos = float(counter)/float(len_ratio)
                pos_cv = cmds.pointOnCurve(
                    closest_curve, top=True, pr=ratio_pos)
                ratio_pos_reversed = float(
                    counter_reversed[counter])/float(len_ratio)
                pos_cv_reversed = cmds.pointOnCurve(
                    closest_curve, top=True, pr=ratio_pos_reversed)
                cord_vertex_to_move = cmds.xform(
                    vertex, worldSpace=True, translation=True, q=True)
                distance_first_direction += math.dist(
                    cord_vertex_to_move, pos_cv)
                distance_other_direction += math.dist(
                    cord_vertex_to_move, pos_cv_reversed)
            for counter, vertex in enumerate(ordered_vertex_loop_path):
                if distance_first_direction < distance_other_direction:
                    ratio_pos = float(counter)/float(len_ratio)
                    pos_cv = cmds.pointOnCurve(
                        closest_curve, top=True, pr=ratio_pos)
                else:
                    ratio_pos_reversed = float(
                        counter_reversed[counter])/float(len_ratio)
                    pos_cv = cmds.pointOnCurve(
                        closest_curve, top=True, pr=ratio_pos_reversed)
                cmds.move(pos_cv[0], pos_cv[1], pos_cv[2], vertex, a=True)
    if list_mesh_to_relax_precisely:
        # workaround for mesh that have a closed curve
        run_relax_sculpt_mode(
            list_mesh_to_relax_precisely, relax_till_done=True)
        for mesh in list_mesh_to_relax_precisely:
            # is the mesh is a closed one sometimes flip
            cmds.polyNormalPerVertex(mesh, xyz=(0, 0, 1))
    run_relax_sculpt_mode(list_geo_to_relax, relax_till_done=False)

    for mirror_parent in dict_patent_and_child_smart_mirror:
        cmds.transferAttributes(
            flatten_ref_geometry, mirror_parent,
            transferPositions=0, transferNormals=0, transferUVs=2,
            transferColors=2, sampleSpace=0, sourceUvSpace="map1",
            targetUvSpace="map1", searchMethod=3, flipUVs=0,
            colorBorders=1)
        cmds.delete(mirror_parent, constructionHistory=1)
    list_mirror_mesh_to_rebind_labels = set()
    for mirror_parent, list_mirror_mesh in dict_patent_and_child_smart_mirror.items():
        if isinstance(list_mirror_mesh, str):
            list_mirror_mesh = {list_mirror_mesh}
        for mirror_mesh in list_mirror_mesh:
            selection_list = om2.MSelectionList()
            selection_list.add(mirror_mesh)
            mfn_mesh_smart_mirror = om2.MFnMesh(selection_list.getDagPath(0))
            cord_point_before_smart_mirror = mfn_mesh_smart_mirror.getPoints()
            cmds.connectAttr(f"{mirror_parent}.outMesh", f"{mirror_mesh}.inMesh",
                             force=True)
            mfn_mesh_smart_mirror.setPoints(cord_point_before_smart_mirror)
            cmds.transferAttributes(
                flatten_ref_geometry, mirror_mesh,
                transferPositions=0, transferNormals=0, transferUVs=2,
                transferColors=2, sampleSpace=0, sourceUvSpace="map1",
                targetUvSpace="map1", searchMethod=3, flipUVs=0,
                colorBorders=1)
            # technically is not necessary but connecting the .outMesh and .inMesh
            # for some reason automatically clean the content of the quick set
            # so is it necessary to recalculate them.
            list_mirror_mesh_to_rebind_labels.add(mirror_mesh)

    bind_label_indicator(list_input_geometry=list_mirror_mesh_to_rebind_labels,
                         bool_create_label=False,
                         bool_unhide_updated_label=False,
                         bool_just_return_found_label_curve=False,
                         bool_check_if_proper_input=True,
                         posed_ref_geometry=posed_ref_geometry,
                         dict_cord_master_uv_point=None,
                         list_perimeter_curve=None)

    for curve in list_perimeter_curve:
        cmds.move(0, 0, 0.001, curve, absolute=True)  # better for hitest()
    for mesh in list_geo_to_relax:
        cmds.setAttr(mesh + '.displayColors', 0)
    cmds.select(list_geo_to_relax)



def create_and_place_mirror(
    list_mesh_to_mirror: List[str],
    flatten_ref_geometry: str,
) -> Tuple[List[List[str]], List[List[str]]]:
    """Placed a mirror of a flatten mesh on another unflatten mesh.

    Args:
        list_mesh_to_mirror (List[str]): mesh to try to position and mirror.
        flatten_ref_geometry (str): the flatten ref version of the flatten geometry to relax.

    Returns:
        Tuple[List[List[str]], List[List[str]]]: the paired couple plus the skipped ones.
    """
    # TODO fix: the two unflatten mesh needs to have the same X rotation

    def percentage_difference(value_01: Union[int, float], value_02: Union[int, float]):
        return abs(value_01 - value_02) / ((value_01 + value_02) / 2) * 100

    if len(list_mesh_to_mirror) % 2 == 1 or not isinstance(list_mesh_to_mirror, list):
        message("You cannot select an odd number of meshes.",
                raise_error=True)
        return
    list_start_mesh = list_mesh_to_mirror[::2]
    list_goal_mesh = list_mesh_to_mirror[1::2]
    for i in range(int(len(list_mesh_to_mirror) / 2)):
        raise_error_if_mesh_is_unflat(list_start_mesh[i])
        raise_error_if_mesh_is_unflat(list_goal_mesh[i])
    # if reach this point you know that both mesh couple have one (and only one) axis that
    # has been flattened. (one of the 3 dimensions is > 0.01)
    list_couple_skipped = []
    list_couple_done = []
    for i in range(int(len(list_mesh_to_mirror) / 2)):
        master_mesh = list_start_mesh[i]
        slave_mesh = list_goal_mesh[i]
        area_start_mesh = cmds.polyEvaluate(master_mesh, wa=True)
        area_goal_mesh = cmds.polyEvaluate(slave_mesh, wa=True)
        area_diff = percentage_difference(area_start_mesh, area_goal_mesh)
        threshold_difference_area = 0.25
        # if the area difference is higher than 0.25% than skip
        if area_diff > threshold_difference_area:
            print("pass area", area_diff)
            list_couple_skipped.append([master_mesh, slave_mesh])
            continue

        bbox_start_mesh = cmds.exactWorldBoundingBox(
            master_mesh, calculateExactly=True)
        bbox_goal_mesh = cmds.exactWorldBoundingBox(
            slave_mesh, calculateExactly=True)
        x_size_start_mesh = abs(
            bbox_start_mesh[3] - bbox_start_mesh[0])
        y_size_start_mesh = abs(
            bbox_start_mesh[4] - bbox_start_mesh[1])
        z_size_start_mesh = abs(
            bbox_start_mesh[5] - bbox_start_mesh[2])

        x_size_goal_mesh = abs(bbox_goal_mesh[3] - bbox_goal_mesh[0])
        y_size_goal_mesh = abs(bbox_goal_mesh[4] - bbox_goal_mesh[1])
        z_size_goal_mesh = abs(bbox_goal_mesh[5] - bbox_goal_mesh[2])

        x_diff, y_diff, z_diff = 0, 0, 0
        # if one of the meshes is completely flat than do not calculate
        # the % difference for that axis
        if x_size_start_mesh != 0 and x_size_goal_mesh != 0:
            x_diff = percentage_difference(
                x_size_start_mesh, x_size_goal_mesh)
        if y_size_start_mesh != 0 and y_size_goal_mesh != 0:
            y_diff = percentage_difference(
                y_size_start_mesh, y_size_goal_mesh)
        if z_size_start_mesh != 0 and z_size_goal_mesh != 0:
            z_diff = percentage_difference(
                z_size_start_mesh, z_size_goal_mesh)

        threshold_difference_size = 0.1
        # if the size difference is higher than 0.1% than skip
        if x_diff > threshold_difference_size:
            print("pass x", x_diff)
            list_couple_skipped.append([master_mesh, slave_mesh])
            continue
        if y_diff > threshold_difference_size:
            print("pass y", y_diff)
            list_couple_skipped.append([master_mesh, slave_mesh])
            continue
        if z_diff > threshold_difference_size:
            print("pass z", z_diff, z_size_start_mesh, z_size_goal_mesh)
            list_couple_skipped.append([master_mesh, slave_mesh])
            continue

        if not cmds.getAttr(f"{master_mesh}.visibility"):
            cmds.setAttr(f"{master_mesh}.visibility", 1)
        if not cmds.getAttr(f"{slave_mesh}.visibility"):
            cmds.setAttr(f"{slave_mesh}.visibility", 1)
        # center pivot only works if the mesh is visible
        cmds.xform(master_mesh, centerPivots=1)
        cmds.xform(slave_mesh, centerPivots=1)

        pos_pivot_start_mesh = [(bbox_start_mesh[3] + bbox_start_mesh[0])/2,
                                ((bbox_start_mesh[4] +
                                    bbox_start_mesh[1])/2),
                                ((bbox_start_mesh[5] + bbox_start_mesh[2])/2)]
        pos_pivot_goal_mesh = [(bbox_goal_mesh[3] + bbox_goal_mesh[0])/2,
                               ((bbox_goal_mesh[4] +
                                 bbox_goal_mesh[1])/2),
                               ((bbox_goal_mesh[5] + bbox_goal_mesh[2])/2)]

        mesh_smart_mirror = cmds.duplicate(master_mesh,
                                           n=f"{master_mesh.split('|')[-1]}_mirror"
                                           )[0]
        cmds.move(pos_pivot_goal_mesh[0]-pos_pivot_start_mesh[0], pos_pivot_goal_mesh[1] -
                  pos_pivot_start_mesh[1],
                  pos_pivot_goal_mesh[2]-pos_pivot_start_mesh[2],
                  mesh_smart_mirror, relative=True)

        edge_border_goal_mesh = add_full_name_to_index_component(
            get_component_on_border(slave_mesh, mode="edge"), geometry_name=slave_mesh, mode="e")
        cmds.select(edge_border_goal_mesh)
        dummy_curve_perimeter_goal_mesh_unflipped = cmds.polyToCurve(
            form=1,  # form=1 (always open)
            degree=1,
            name="dummy_curve_perimeter_goal_mesh_unflipped",
            conformToSmoothMeshPreview=1,
            constructionHistory=False,
        )[0]
        cmds.setAttr(f"{slave_mesh}.scaleX",
                     cmds.getAttr(f"{slave_mesh}.scaleX") * -1)
        cmds.select(edge_border_goal_mesh)
        dummy_curve_perimeter_goal_mesh_flipped = cmds.polyToCurve(
            form=1,  # form=1 (always open)
            degree=1,
            name="dummy_curve_perimeter_goal_mesh_flipped",
            conformToSmoothMeshPreview=1,
            constructionHistory=False,
        )[0]
        cmds.setAttr(f"{slave_mesh}.scaleX",
                     cmds.getAttr(f"{slave_mesh}.scaleX")*-1)

        selection_list = om2.MSelectionList()
        selection_list.add(dummy_curve_perimeter_goal_mesh_unflipped)
        mfn_curve_unflipped = om2.MFnNurbsCurve(
            selection_list.getDagPath(0))
        selection_list.add(dummy_curve_perimeter_goal_mesh_flipped)
        mfn_curve_flipped = om2.MFnNurbsCurve(
            selection_list.getDagPath(1))
        selection_list.add(mesh_smart_mirror)
        mfn_smart_mirror = om2.MFnMesh(selection_list.getDagPath(2))

        distance_start_mesh_unflipped = 0
        distance_start_mesh_flipped = 0
        for vertex in get_component_on_border(master_mesh, mode="vtx"):
            pos_vtx_point_smart_mirror = mfn_smart_mirror.getPoint(
                vertex, space=om2.MSpace.kWorld)
            distance_start_mesh_unflipped += mfn_curve_unflipped.distanceToPoint(
                pos_vtx_point_smart_mirror, space=om2.MSpace.kWorld)
            distance_start_mesh_flipped += mfn_curve_flipped.distanceToPoint(
                pos_vtx_point_smart_mirror, space=om2.MSpace.kWorld)

        cmds.delete((dummy_curve_perimeter_goal_mesh_unflipped,
                    dummy_curve_perimeter_goal_mesh_flipped))

        if distance_start_mesh_unflipped > distance_start_mesh_flipped:
            cmds.setAttr(f"{mesh_smart_mirror}.scaleX",
                         cmds.getAttr(f"{mesh_smart_mirror}.scaleX")*-1)

        cmds.connectAttr(
            f"{master_mesh}.outMesh", f"{mesh_smart_mirror}.inMesh", force=True)
        cmds.transferAttributes(
            flatten_ref_geometry, mesh_smart_mirror,
            transferPositions=0, transferNormals=0, transferUVs=2,
            transferColors=2, sampleSpace=0, sourceUvSpace="map1",
            targetUvSpace="map1", searchMethod=3, flipUVs=0,
            colorBorders=1)
        # cosmetics changes
        cmds.setAttr(f"{slave_mesh}.visibility", 0)
        cmds.setAttr(f'{master_mesh}.useOutlinerColor', True)
        cmds.setAttr(
            f'{master_mesh}.outlinerColor', 0, 1, 0, 'float3')
        cmds.setAttr(
            f'{master_mesh}.overrideColorRGB', 0, 0.3, 0, 'float3')
        cmds.setAttr(f'{master_mesh}.overrideRGBColors', True)
        cmds.setAttr(f'{master_mesh}.overrideEnabled', True)
        cmds.setAttr(f'{mesh_smart_mirror}.useOutlinerColor', True)
        cmds.setAttr(f'{mesh_smart_mirror}.outlinerColor',
                     0, 0, 1, 'float3')
        cmds.setAttr(f'{mesh_smart_mirror}.overrideDisplayType', 2)
        cmds.setAttr(f'{mesh_smart_mirror}.overrideEnabled', True)

        list_couple_done.append([master_mesh, mesh_smart_mirror])

    if list_couple_skipped:
        message(
            f"Some couple was skipped. {len(list_couple_skipped)}", raise_error=False)
    return list_couple_done, list_couple_skipped


def reconstruct_mesh(
    list_geo_to_reconstruct: Union[List[str], Set[str]],
    posed_ref_geometry: str,
    flatten_ref_geometry: str,
) -> str:
    """Reconstruct the original mesh when done the retopo of the flatten uv shell.

    Args:
        list_geo_to_reconstruct (Union[Union[List[str], Set[str]]]): flatten uv shell to pose.
        posed_ref_geometry (str): the posed ref version of the flatten geometry to relax.
        flatten_ref_geometry (str): the flatten ref version of the flatten geometry to relax.
    """
    # TODO: bug fix. the cone do not work. Closed curve not supported
    # TODO: new function: position the vertex in the exact position and not an approximate one
    def percentage_difference(value_01: Union[int, float], value_02: Union[int, float]):
        return abs(value_01 - value_02) / ((value_01 + value_02) / 2) * 100

    area_flatten_ref = cmds.polyEvaluate(
        flatten_ref_geometry, worldArea=True)

    if len(list_geo_to_reconstruct) != cmds.polyEvaluate(posed_ref_geometry, uvShell=True):
        message("the number of UV shell do not match up", raise_error=True)
    area_uv_flatten_ref = cmds.polyEvaluate(
        flatten_ref_geometry, uvArea=True)
    area_selected_mesh = float()
    area_uv_selected_mesh = float()
    for mesh in list_geo_to_reconstruct:
        area_selected_mesh += cmds.polyEvaluate(mesh, worldArea=True)
        area_uv_selected_mesh += cmds.polyEvaluate(mesh, uvArea=True)

    area_diff = percentage_difference(area_flatten_ref, area_selected_mesh)
    if area_diff > 0.1:
        message(
            f"the area difference is too different: {str(area_diff)}", raise_error=True)
    area_uv_diff = percentage_difference(
        area_uv_flatten_ref, area_uv_selected_mesh)
    if area_uv_diff > 0.1:
        message(f"the UV area difference is too different: {str(area_uv_diff)}",
                raise_error=True)

    for mesh in list_geo_to_reconstruct:
        raise_error_if_mesh_has_missing_uvs(mesh)
        raise_error_if_mesh_is_unflat(mesh)
        raise_error_if_mesh_has_overlapping_uvs(mesh)

    list_label_curve = bind_label_indicator(
        list_input_geometry=list_geo_to_reconstruct,
        bool_create_label=False,
        bool_unhide_updated_label=False,
        bool_just_return_found_label_curve=True,
        bool_check_if_proper_input=True,
        posed_ref_geometry=posed_ref_geometry,
        dict_cord_master_uv_point=None,
        list_perimeter_curve=None)
    for label_curve in list_label_curve:
        if cmds.getAttr(label_curve + '.overrideColor') == 13:
            message("The topology is not right.", raise_error=True)
    mesh_to_recontract = duplicate_mesh_without_set(list_geo_to_reconstruct)
    set_extra_shape_node_to_delete = set()
    for mesh in mesh_to_recontract:
        # if the mesh could have extra shape node. Those extra shape nodes
        # create ugly empty groups node even when running cmds.polyUnite()
        extra_shape_node_to_delete = cmds.listRelatives(
            mesh, shapes=True, path=True)
        set_extra_shape_node_to_delete.update(extra_shape_node_to_delete[1:])
    cmds.delete(set_extra_shape_node_to_delete)
    mesh_merged = cmds.polyUnite(
        mesh_to_recontract, constructionHistory=0, centerPivot=1)[0]
    cmds.delete(mesh_merged, constructionHistory=True)
    cmds.transferAttributes(posed_ref_geometry, mesh_merged,
                            transferPositions=1, transferNormals=0, transferUVs=2,
                            transferColors=2, sampleSpace=3, sourceUvSpace="map1",
                            targetUvSpace="map1", searchMethod=3, flipUVs=0,
                            colorBorders=1)
    cmds.delete(mesh_merged, constructionHistory=True)
    dict_cord_master_uv_point = dict_cord_master_uv_points_from_posed_mesh(
        posed_ref_geometry)
    list_input_index_master_uv_point = re_find_uv_master_point(
        dict_cord_master_uv_point, dict_uv_cord_to_compare_to(mesh_merged), 0.0001)
    list_edge_loop_path = create_curve(
        mesh_merged,
        list_input_index_master_uv_point,
        just_return_list_edge_loop_full_name=True
    )
    dict_curve_and_list_vertex_loop = {}
    for edge_loop_path in list_edge_loop_path:
        ordered_vertex_loop_path = ordered_vertex_loop_from_edge_loop(
            list(edge_loop_path))
        # in this case the mesh is posed so it is better to disable form=2 (best guess)
        # and use form=0 (always open)
        cmds.select(edge_loop_path)
        output_curve = cmds.polyToCurve(
            edge_loop_path,
            form=0,
            degree=1,
            name=f"{mesh_merged.split('|')[-1]}_dummy_01",
            conformToSmoothMeshPreview=1,
            constructionHistory=False,
        )[0]
        cmds.rebuildCurve(output_curve, ch=0,
                          s=len(ordered_vertex_loop_path)-1, d=1, tol=0)
        dict_curve_and_list_vertex_loop[output_curve] = ordered_vertex_loop_path

    for output_curve, ordered_vertex_loop_path in dict_curve_and_list_vertex_loop.items():
        # Check if reversing the curve is needed.
        # It the vertex would travel less in 3D space the curve is reversed.
        distance_first_direction = float()
        distance_other_direction = float()
        counter_reversed = list(
            range(len(ordered_vertex_loop_path)))[::-1]
        for counter, vertex in enumerate(ordered_vertex_loop_path):
            pos_cv = cmds.xform(
                f"{output_curve}.ep[{counter}]", worldSpace=True, translation=True, q=True)
            pos_cv_reversed = cmds.xform(
                f"{output_curve}.ep[{counter_reversed[counter]}]", worldSpace=True,
                translation=True, q=True)
            cord_vertex_to_move = cmds.xform(
                vertex, worldSpace=True, translation=True, q=True)
            distance_first_direction += math.dist(
                cord_vertex_to_move, pos_cv)
            distance_other_direction += math.dist(
                cord_vertex_to_move, pos_cv_reversed)
        for counter, vertex in enumerate(ordered_vertex_loop_path):
            if distance_first_direction < distance_other_direction:
                pos_cv = cmds.xform(
                    f"{output_curve}.ep[{counter}]",
                    worldSpace=True, translation=True, q=True)
            else:
                pos_cv = cmds.xform(
                    f"{output_curve}.ep[{counter_reversed[counter]}]",
                    worldSpace=True, translation=True, q=True)
            cmds.move(pos_cv[0], pos_cv[1], pos_cv[2], vertex, a=True)
        cmds.delete(output_curve)

    cmds.polySetToFaceNormal(mesh_merged)
    list_geo_to_relax = cmds.polySeparate(
        mesh_merged, name=f"{mesh_merged.split('|')[-1]}_shell_01",
        constructionHistory=False)
    cmds.makeLive(posed_ref_geometry)
    run_relax_sculpt_mode(list_geo_to_relax, relax_till_done=False)
    cmds.makeLive(none=True)
    mesh_merged = cmds.polyUnite(
        list_geo_to_relax, constructionHistory=False, centerPivot=1)[0]
    cmds.delete(mesh_merged, constructionHistory=True)
    mesh_merged = cmds.rename(mesh_merged, f"{posed_ref_geometry}_retopo")

    vertexes_border_merged_mesh = add_full_name_to_index_component(
        get_component_on_border(mesh_merged, mode="vtx"), geometry_name=mesh_merged, mode="vtx")
    cmds.polyMergeVertex(
        vertexes_border_merged_mesh,
        distance=1,
        worldSpace=False,
        constructionHistory=True,  # by default merge but the user could edit the manipulator UI
        )
    cmds.select(clear=True)
    # cosmetics changes
    cmds.setAttr(f'{mesh_merged}.useOutlinerColor', True)
    cmds.setAttr(f'{mesh_merged}.outlinerColor', 1, 0, 0, 'float3')
    cmds.ShowManipulators(mesh_merged) # make clear that the vtx merged can be reversed if needed

    return mesh_merged
#
# How the zremesh works inside Maya:
#
# The upper part of the UI is for the remesher an the other it is specific for garments.
# When the zremesh button is pressed write a .txt file made in zscript
# containing the setting for the zremesh.
# Maya export the mesh to .ma or .GoZ (depends if the GoZ plugin is installed or not).
# Then Maya launch 'start_zbrush_bridge.zsc' that compile and execute the just written .txt file.
# Zbrush do the zremesh and export the result in .obj. Also export a star mesh in .obj.
# In the meantime Maya is waiting for the dummy mesh to be create.
# When done Maya will import the zremesh output mesh.
# For each polygroup a material with a random color will be created.
#
# The limitations of this approach:
#
#+------+--------+-----------+-----------+--------------------+-----------------+-----------------+
#| file | crease | polypaint | polygroup | works with Zscript | Maya importable | requires plugin |
#+------+--------+-----------+-----------+--------------------+-----------------+-----------------+
#| .ma  | yes    | no        | no        | yes                | no              | no              |
#| .obj | no     | no        | yes       | yes                | yes             | no              |
#| .fbx | yes    | yes       | yes       | no                 | yes             | no              |
#| .ply | no     | yes       | no        | kinda              | no              | no              |
#| .goz | yes    | yes       | yes       | yes                | yes             | yes             |
#+------+--------+-----------+-----------+--------------------+-----------------+-----------------+
#
# The only way for exporting the crease data out of zbrush it to use .goz.
# The .obj support the polygroup in both ways (if enabled in the settings) but not the crease.
# The .fbx support the polygroup in both ways and the the crease but do not work with zscript.
# The .ma appears to be unreadable to Maya when exporting from Zbrush but support the crease
# data when importing in Zbrush. Unfortunately do not support the polygroup and vertex color
# but keep the crease.
# the .goz support everything but requires the plugin. Pixologic must update it for each Maya.
# GoB for Blender write the binary .GoZ without SDK but is slower and much more complex.
# I wrote a definition for exporting .ply out of Maya with color data but when imported Zbrush
# stop the Zscript execution (bug). Otherwise the combo .ma and .ply would have been fine.
# .ma give the crease data and the ply the color information. Just project the color data onto
# the .ma with the crease. When the .ply is imported Zbrush automatically write a .GoZ file in
# a temp dir "C:\Users\Public\Documents\ZBrushData2023\ZPluginData\ExportImportData".
# Scan that folder and wait until the .GoZ file is not locked anymore than run a second Zscript.
# GoZ plugin is fine considering the alternatives. If GoZ is not installed the only limitation
# is that you cannot transfer polypaint information to Zbrush (fall back to .ma).
#
# - Requires Zbrush running in the background. You should not sculpt in Zbrush while using this.
# If for some reason you need to do so it should be possible to use one version of Zbrush
# only for the zremesh and the other one could be used for sculpting.
# Technically running Zbrush in the background is not even required. It would be enough to uses
# a CLI exe that lives here: C:\Program Files\Maxon ZBrush 2023\ZData\ZPlugs64\zrem\zremesh.exe
# When the users click on zremesh Zbrush create in a tmp dir containing the input mesh the output
# mesh as well as a .txt file containing all the settings for the zremesh.
# I would be sufficient to covert the mesh in .goz with Maya then write the zremesh settings into
# that txt file. At this point the utility should output the output mesh in .goz
# Of course reverse engineering that .exe would probably be a legal grey zone.
#
# - The user must compile the .txt into .zsc the very first time that he want to zremesh in Maya.
#
# - The polygroup information are maintained even using Maya ascii for exporting from maya
#  and obj from exporting from Zbrush. But there is a performance hit for making it possible.
#
# - Only works with python3 Maya and Zbrush 2023. Mac os X is not supported.
#


class Zr4mWindow(QMainWindow):
    """Create the main window"""

    def __init__(self, list_path):
        super().__init__()

        maya_main_window_ptr = OpenMayaUI.MQtUtil.mainWindow()
        maya_main_window = wrapInstance(int(maya_main_window_ptr), QMainWindow)
        self.setParent(maya_main_window)
        self.setWindowFlags(Qt.Window)
        self.setObjectName('ZR4M')
        self.setWindowTitle("ZR4M")
        # remember window position
        #self.setProperty("saveWindowPref", True)
        # disallow resizing
        self.setWindowFlags(self.windowFlags() |
                            Qt.MSWindowsFixedSizeDialogHint)

        (self.master_dir, self.tmp_dir, self.start_zbrush_bridge_zsc,
         self.start_zbrush_bridge_txt, self.zremesh_settings) = list_path
        self.output_maya_ascii = self.tmp_dir / "tmp_output_maya.ma"
        self.output_maya_goz = self.tmp_dir / "tmp_output_maya.GoZ"
        self.output_zbrush = self.tmp_dir / "tmp_output_zbrush.obj"
        self.dummy_geo_retry_disabled = self.tmp_dir / "tmp_dummy_geo_retry_disabled.obj"
        self.dummy_geo_zremesh_failed = self.tmp_dir / "tmp_dummy_geo_zremesh_failed.obj"
        self.dummy_geo_zremesh_done = self.tmp_dir / "tmp_dummy_geo_zremesh_done.obj"
        self.zbrush_exe_location = Path(
            r"C:\Program Files\Maxon ZBrush 2023\ZBrush.exe")

        self.button_html_style = 'QPushButton {background-color: "Dark Orange"; color: Black;}'

        self.dict_timer_keep_focus = {}
        self.timer_wait_for_zremesh = {}
        self.name_last_maya_exported_geo = None
        self.but_pressed = None
        self.uuid_object_last_zremesh_output = None
        self.uuid_last_maya_exported_geo = None
        self.list_vtx_cord_geo_input_zremesh = None
        self.list_vtx_cord_geo_last_output_zremesh = None

        self.job_check_existences_flat_ref = None
        self.full_name_flat_ref_geo = None
        self.job_check_existences_posed_ref = None
        self.full_name_posed_ref_geo = None

        self.create_layout()
        self.create_connections()
        self.start_tracking_cursor()
        self.job_unselect_curves = cmds.scriptJob(
            event=["SelectionChanged", self.job_event_selection_changed])
        if not [curve for curve in return_curve_in_scene()[0]
                if cmds.attributeQuery("connection_between_indicator_and_label_curve",
                                       node=curve, exists=True)]:
            self.but_toggle_label.setEnabled(False)
            self.but_rebind_label.setEnabled(False)

        # nice for usability. Can be removed if you do not like global variables.
        if "last_posed_ref_geo_created" in globals():
            try:
                self.set_posed_ref_geo(last_posed_ref_geo_created)
            except:  # pylint: disable=bare-except
                pass
        if "last_unwrapped_ref_geo_created" in globals():
            current_scene_unit = cmds.currentUnit(query=True, linear=True)
            try:
                self.set_flat_ref_geo(last_unwrapped_ref_geo_created)
            except:  # pylint: disable=bare-except
                pass
            finally:
                cmds.currentUnit(linear=current_scene_unit)
        if ("last_unwrapped_ref_geo_created" in globals()
                or "last_posed_ref_geo_created" in globals()):
            self.groupbox_garment.setChecked(True)

        if not cmds.pluginInfo('gozMaya', query=True, loaded=True):
            try:
                cmds.loadPlugin('gozMaya')
            except RuntimeError:
                self.is_goz_installed = False
                self.but_use_polypaint.setEnabled(False)
            else:
                self.is_goz_installed = True
        else:
            self.is_goz_installed = True

        print(f"{self.windowTitle()} window started")

    def create_layout(self):
        """Create the layout"""
        # main layout
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        widget = QWidget()
        widget.setLayout(main_layout)
        self.setCentralWidget(widget)

        # create zremesh group box
        self.groupbox_zremesh = QGroupBox("ZRemesh Bride for Maya", self)
        groupbox_zremesh_layout = QVBoxLayout()
        self.groupbox_zremesh.setLayout(groupbox_zremesh_layout)
        main_layout.addWidget(self.groupbox_zremesh)
        ###

        # create Curve Strength slider
        # horizontal layout for text and counter
        smooth_groups_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(smooth_groups_layout)
        self.label_smooth_groups = QLabel('Smooth groups:')
        self.spin_smooth_groups = QDoubleSpinBox()
        self.spin_smooth_groups.setValue(0)
        self.spin_smooth_groups.setRange(0, 1)
        self.spin_smooth_groups.setSingleStep(0.01)
        smooth_groups_layout.addWidget(self.label_smooth_groups)
        smooth_groups_layout.addWidget(self.spin_smooth_groups)
        # vertical layout with the two previous labels plus a slider
        smooth_groups_layout_master = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(smooth_groups_layout_master)
        self.slider_smooth_groups = QSlider(Qt.Horizontal)
        self.slider_smooth_groups.setValue(0)
        self.slider_smooth_groups.setRange(0, 100)
        self.slider_smooth_groups.setSingleStep(1)
        smooth_groups_layout_master.addWidget(self.slider_smooth_groups)
        ###

        # create grid layout with 8 slot
        grid_layout_zremesh = QGridLayout()
        groupbox_zremesh_layout.addLayout(grid_layout_zremesh)
        # fix the stretch grid
        grid_layout_zremesh.setColumnStretch(0, 5)
        grid_layout_zremesh.setColumnStretch(1, 5)
        #
        self.but_freeze_border = QPushButton('Freeze Border')
        self.but_freeze_groups = QPushButton('Freeze Groups')
        self.but_keep_groups = QPushButton('Keep Groups')
        self.but_keep_groups.setStyleSheet(self.button_html_style)
        self.but_keep_creases = QPushButton('Keep Creases')
        self.but_keep_creases.setStyleSheet(self.button_html_style)
        self.but_detect_edges = QPushButton('Detect Edges')
        self.but_detect_edges.setStyleSheet(self.button_html_style)
        self.but_use_polypaint = QPushButton('Use Polypaint')

        grid_layout_zremesh.addWidget(self.but_keep_groups, 0, 0)
        grid_layout_zremesh.addWidget(self.but_freeze_border, 1, 0)
        grid_layout_zremesh.addWidget(self.but_freeze_groups, 1, 1)
        grid_layout_zremesh.addWidget(self.but_keep_creases, 0, 1)
        grid_layout_zremesh.addWidget(self.but_detect_edges, 3, 0)
        grid_layout_zremesh.addWidget(self.but_use_polypaint, 3, 1)
        ###

        # create Target Quad Count slider
        # horizontal layout for text and counter
        target_quad_count_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(target_quad_count_layout)
        self.label_target_quad = QLabel('Target Quad Count:')

        self.spin_quad_count = QDoubleSpinBox()
        self.spin_quad_count.setValue(1)
        self.spin_quad_count.setRange(0.1, 100)
        self.spin_quad_count.setSingleStep(0.01)
        target_quad_count_layout.addWidget(self.label_target_quad)
        target_quad_count_layout.addWidget(self.spin_quad_count)
        # vertical layout with the two previous labels plus a slider
        target_quad_count_layout_master = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(target_quad_count_layout_master)
        self.slider_quad_count = QSlider(Qt.Horizontal)
        self.slider_quad_count.setValue(1)
        self.slider_quad_count.setRange(0.1, 100)
        self.slider_quad_count.setSingleStep(0.1)
        target_quad_count_layout_master.addWidget(self.slider_quad_count)
        ###

        # layout with four horizontal button
        four_row_button_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(four_row_button_layout)

        self.but_half = QPushButton('Half')
        self.but_same = QPushButton('Same')
        self.but_double = QPushButton('Double')
        self.but_adapt = QPushButton('Adapt')
        self.but_adapt.setStyleSheet(self.button_html_style)

        four_row_button_layout.addWidget(self.but_half)
        four_row_button_layout.addWidget(self.but_same)
        four_row_button_layout.addWidget(self.but_double)
        four_row_button_layout.addWidget(self.but_adapt)

        # create Adaptive Size slider
        # horizontal layout for text and counter
        adaptive_side_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(adaptive_side_layout)
        self.label_adaptive_size = QLabel('Adaptive Size:')

        self.spin_adaptive_size = QSpinBox()
        self.spin_adaptive_size.setValue(50)
        self.spin_adaptive_size.setRange(0, 100)
        self.spin_adaptive_size.setSingleStep(1)

        adaptive_side_layout.addWidget(self.label_adaptive_size)
        adaptive_side_layout.addWidget(self.spin_adaptive_size)
        # vertical layout with the two previous labels plus a slider
        adaptive_side_layout_master = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(adaptive_side_layout_master)
        self.slider_adaptive_size = QSlider(Qt.Horizontal)
        self.slider_adaptive_size.setValue(50)
        self.slider_adaptive_size.setRange(0, 100)
        self.slider_adaptive_size.setSingleStep(1)
        adaptive_side_layout_master.addWidget(self.slider_adaptive_size)
        ###

        # create Color density slider
        # horizontal layout for text and counter
        color_density_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(color_density_layout)
        self.label_adaptive_size = QLabel('Color density:')

        self.spin_color_density = QDoubleSpinBox()
        self.spin_color_density.setValue(2)
        self.spin_color_density.setRange(0.25, 4)
        self.spin_color_density.setSingleStep(0.1)

        color_density_layout.addWidget(self.label_adaptive_size)
        color_density_layout.addWidget(self.spin_color_density)
        # vertical layout with the two previous labels plus a slider
        color_density_layout_master = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(color_density_layout_master)
        self.slider_color_density = QSlider(Qt.Horizontal)
        self.slider_color_density.setValue(200)
        self.slider_color_density.setRange(25, 400)
        self.slider_color_density.setSingleStep(1)
        color_density_layout_master.addWidget(self.slider_color_density)
        ###

        # create symmetry checkboxes
        sym_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(sym_layout)

        self.symmetry_x = QCheckBox()
        self.symmetry_x.setText("X")
        self.symmetry_x.setChecked(False)
        sym_layout.addWidget(self.symmetry_x)

        self.symmetry_y = QCheckBox()
        self.symmetry_y.setText("Y")
        self.symmetry_y.setChecked(False)
        sym_layout.addWidget(self.symmetry_y)

        self.symmetry_z = QCheckBox()
        self.symmetry_z.setText("Z")
        self.symmetry_z.setChecked(False)
        sym_layout.addWidget(self.symmetry_z)

        self.symmetry_r = QCheckBox()
        self.symmetry_r.setText("R")
        self.symmetry_r.setChecked(False)
        self.spin_radial_sym = QSpinBox()
        self.spin_radial_sym.setEnabled(False)
        self.spin_radial_sym.setValue(2)
        self.spin_radial_sym.setRange(2, 100)
        sym_layout.addWidget(self.symmetry_r)
        sym_layout.addWidget(self.spin_radial_sym)

        # create button for ZRemesh
        zremesh_layout = QHBoxLayout()
        groupbox_zremesh_layout.addLayout(zremesh_layout)
        self.but_zremesh = QPushButton('ZRemesh')
        self.but_retry = QPushButton('Retry')
        self.but_abort_zremesh = QPushButton('Abort')

        self.but_zremesh.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.but_retry.setSizePolicy(
            QSizePolicy.Minimum, QSizePolicy.Preferred)

        self.but_zremesh.setStyleSheet("QPushButton{font-size: 14pt;}")
        self.but_retry.setStyleSheet("QPushButton{font-size: 14pt;}")
        self.but_abort_zremesh.setStyleSheet("QPushButton{font-size: 14pt;}")

        zremesh_layout.addWidget(self.but_zremesh)
        zremesh_layout.addWidget(self.but_retry)
        zremesh_layout.addWidget(self.but_abort_zremesh)

        self.but_abort_zremesh.hide()
        self.but_retry.setEnabled(False)
        ###

        # create master group box
        self.groupbox_garment = QGroupBox(
            "Show utilities for garment retopo", self)
        self.groupbox_garment.setCheckable(True)
        self.groupbox_garment.setChecked(False)
        groupbox_garment_layout = QVBoxLayout()
        self.groupbox_garment.setLayout(groupbox_garment_layout)
        main_layout.addWidget(self.groupbox_garment)
        ###

        # button for set ref mesh
        button_set_ref_layout = QHBoxLayout()
        groupbox_garment_layout.addLayout(button_set_ref_layout)

        self.but_set_flat_ref = QPushButton('Set flatten ref')
        self.but_set_posed_ref = QPushButton('Set posed ref')
        self.but_set_flat_ref.hide()
        self.but_set_posed_ref.hide()
        #
        # flat label
        flat_reference_label_layout = QHBoxLayout()
        groupbox_garment_layout.addLayout(flat_reference_label_layout)

        self.label_flat = QLabel(
            '<p><strong>Flatten ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>'
            '<font color="red">No geometry found</font></p>'
        )
        self.label_flat.hide()
        #
        # posed label
        posed_reference_label_layout = QHBoxLayout()
        groupbox_garment_layout.addLayout(posed_reference_label_layout)
        self.label_posed = QLabel(
            '<p><strong>Posed ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>'
            '<font color="red">No geometry found</font></p>'
        )
        self.label_posed.hide()
        #
        # button transfer attribute
        button_transfer_attribute = QHBoxLayout()
        groupbox_garment_layout.addLayout(button_transfer_attribute)

        self.but_uv_from_flat_ref = QPushButton('UV from flatten ref')
        self.but_position_from_posed_ref = QPushButton('Pos from posed ref')
        self.but_uv_from_flat_ref.setEnabled(False)
        self.but_position_from_posed_ref.setEnabled(False)
        self.but_uv_from_flat_ref.hide()
        self.but_position_from_posed_ref.hide()
        #
        # unwrap
        self.but_unwrap = QPushButton('Unwrap')
        self.but_unwrap_and_analyze = QPushButton('Unwrap analyze')
        self.but_unwrap.hide()
        self.but_unwrap_and_analyze.hide()
        #
        # button that requires at least one reference object
        self.but_relax_flat = QPushButton('Relax flat mesh')
        self.but_relax_flat.setEnabled(False)
        self.but_relax_flat.hide()
        self.but_create_mirror = QPushButton('Create mirror')
        self.but_rebind_label = QPushButton('Rebind labels')
        self.but_toggle_label = QPushButton('Toggle all labels')
        self.but_reconstruct_mesh = QPushButton('Reconstruct mesh')
        self.but_rebind_label.setEnabled(False)
        self.but_create_mirror.setEnabled(False)
        self.but_reconstruct_mesh.setEnabled(False)
        self.but_create_mirror.hide()
        self.but_toggle_label.hide()
        self.but_rebind_label.hide()
        self.but_reconstruct_mesh.hide()
        #
        # grid layout garment
        grid_layout_garment = QGridLayout()
        groupbox_garment_layout.addLayout(grid_layout_garment)
        grid_layout_garment.setColumnStretch(0, 5)
        grid_layout_garment.setColumnStretch(1, 5)

        if 0:  # NOTE change me if you want a more compact layout
            button_set_ref_layout.addWidget(self.but_set_flat_ref)
            button_set_ref_layout.addWidget(self.but_set_posed_ref)

            button_transfer_attribute.addWidget(self.but_uv_from_flat_ref)
            button_transfer_attribute.addWidget(
                self.but_position_from_posed_ref)

            flat_reference_label_layout.addWidget(self.label_flat)
            posed_reference_label_layout.addWidget(self.label_posed)
            spacer_item_00 = QSpacerItem(0, 0)
            spacer_item_01 = QSpacerItem(0, 0)
        else:
            button_set_ref_layout.addWidget(self.but_set_flat_ref)
            button_set_ref_layout.addWidget(self.but_set_posed_ref)

            flat_reference_label_layout.addWidget(self.label_flat)
            posed_reference_label_layout.addWidget(self.label_posed)
            spacer_item_00 = QSpacerItem(0, 0)
            spacer_item_01 = QSpacerItem(0, 0)

        grid_layout_garment.addItem(spacer_item_00, 0, 0)
        grid_layout_garment.addWidget(self.but_unwrap, 1, 0)
        grid_layout_garment.addWidget(self.but_unwrap_and_analyze, 1, 1)
        grid_layout_garment.addItem(spacer_item_01, 2, 0)
        grid_layout_garment.addWidget(self.but_relax_flat, 3, 0)
        grid_layout_garment.addWidget(self.but_create_mirror, 3, 1)
        grid_layout_garment.addWidget(self.but_rebind_label, 4, 0)
        grid_layout_garment.addWidget(self.but_toggle_label, 4, 1)
        grid_layout_garment.addWidget(self.but_reconstruct_mesh, 5, 0)
        ###

    def create_connections(self):
        """create the connections between the buttons of the UI"""

        # zremesh bridge
        self.but_freeze_border.clicked.connect(self.but_freeze_border_clicked)
        self.but_freeze_groups.clicked.connect(self.but_freeze_groups_clicked)
        self.but_keep_groups.clicked.connect(self.but_keep_groups_clicked)
        self.but_keep_creases.clicked.connect(self.but_keep_creases_clicked)
        self.but_detect_edges.clicked.connect(self.but_detect_edges_clicked)
        self.but_use_polypaint.clicked.connect(self.but_use_polypaint_clicked)
        self.but_half.clicked.connect(self.but_half_clicked)
        self.but_same.clicked.connect(self.but_same_clicked)
        self.but_double.clicked.connect(self.but_double_clicked)
        self.but_adapt.clicked.connect(self.but_adapt_clicked)

        self.symmetry_r.toggled.connect(self.spin_radial_sym.setEnabled)

        self.spin_quad_count.valueChanged.connect(
            self.slider_quad_count.setValue)
        self.slider_quad_count.valueChanged.connect(
            self.spin_quad_count.setValue)

        self.spin_adaptive_size.valueChanged.connect(
            self.slider_adaptive_size.setValue)
        self.slider_adaptive_size.valueChanged.connect(
            self.spin_adaptive_size.setValue)

        self.spin_color_density.valueChanged.connect(
            self.spin_color_density_change)
        self.slider_color_density.valueChanged.connect(
            self.slider_color_density_change)

        self.spin_smooth_groups.valueChanged.connect(
            self.spin_smooth_groups_change)
        self.slider_smooth_groups.valueChanged.connect(
            self.slider_smooth_groups_change)

        self.but_zremesh.clicked.connect(self.do_zremesh)
        self.but_retry.clicked.connect(self.do_zremesh)
        self.but_abort_zremesh.clicked.connect(self.but_abort_zremesh_clicked)

        # garment retopo utilities
        self.groupbox_garment.toggled.connect(
            self.toggle_garment_utilities_visibility)
        self.but_set_flat_ref.clicked.connect(
            self.but_set_flat_ref_geo_clicked)
        self.but_set_posed_ref.clicked.connect(
            self.but_set_posed_ref_geo_clicked)
        self.but_uv_from_flat_ref.clicked.connect(
            self.but_uv_from_flat_ref_geo_clicked)
        self.but_position_from_posed_ref.clicked.connect(
            self.but_position_from_posed_ref_geo_clicked)
        self.but_relax_flat.clicked.connect(self.but_relax_flat_geo_clicked)
        self.but_unwrap.clicked.connect(self.but_unwrap_clicked)
        self.but_unwrap_and_analyze.clicked.connect(
            self.but_unwrap_and_analyze_clicked)
        self.but_create_mirror.clicked.connect(
            self.but_create_mirror_clicked)
        self.but_rebind_label.clicked.connect(
            self.but_rebind_label_clicked)
        self.but_toggle_label.clicked.connect(
            self.but_toggle_label_clicked)
        self.but_reconstruct_mesh.clicked.connect(
            self.but_reconstruct_mesh_clicked)

    def slider_color_density_change(self):
        """Link the slider color density to the spin color density."""
        value_slider_color_density = self.slider_color_density.value()
        self.spin_color_density.setValue(value_slider_color_density / 100)

    def spin_color_density_change(self):
        """Link the spin color density to the slider color density."""
        value_slider_color_density = self.spin_color_density.value()
        self.slider_color_density.setValue(value_slider_color_density * 100)

    def slider_smooth_groups_change(self):
        """Link the slider smooth groups to the spin smooth group."""
        value_slider_smooth_groups = self.slider_smooth_groups.value()
        self.spin_smooth_groups.setValue(value_slider_smooth_groups / 100)

    def spin_smooth_groups_change(self):
        """Link the spin smooth groups to the slider smooth groups."""
        value_spin_smooth_groups = self.spin_smooth_groups.value()
        self.slider_smooth_groups.setValue(value_spin_smooth_groups * 100)

    def toggle_garment_utilities_visibility(self):
        """Toggle the visibility of the garment tools when not required."""
        for widget in self.groupbox_garment.findChildren(QWidget):
            widget.setVisible(not widget.isVisible())
        counter = 0
        while counter < 100:  # https://stackoverflow.com/questions/28202737
            QApplication.processEvents()
            counter += 1
        self.adjustSize()

    def is_button_customized(self, input_button: QPushButton) -> bool:
        """Return if a button is customized or not.

        Args:
            input_button (QPushButton): the button to check against.

        Returns:
            bool: True if the button is customized. False otherwise. 
        """
        if self.button_html_style in input_button.styleSheet():
            return True
        return False

    def but_freeze_border_clicked(self):
        """Style button when clicked."""
        if self.is_button_customized(self.but_freeze_border):
            self.but_freeze_border.setStyleSheet('')
        else:
            self.but_freeze_border.setStyleSheet(self.button_html_style)
            self.but_freeze_groups.setStyleSheet('')

    def but_freeze_groups_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_freeze_groups):
            self.but_freeze_groups.setStyleSheet('')
        else:
            self.but_freeze_groups.setStyleSheet(self.button_html_style)
            self.but_freeze_border.setStyleSheet('')

    def but_keep_groups_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_keep_groups):
            self.but_keep_groups.setStyleSheet('')
            self.spin_smooth_groups.setEnabled(False)
            self.slider_smooth_groups.setEnabled(False)
        else:
            self.but_keep_groups.setStyleSheet(self.button_html_style)
            self.spin_smooth_groups.setEnabled(True)
            self.slider_smooth_groups.setEnabled(True)

    def but_keep_creases_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_keep_creases):
            self.but_keep_creases.setStyleSheet('')
        else:
            self.but_keep_creases.setStyleSheet(self.button_html_style)

    def but_detect_edges_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_detect_edges):
            self.but_detect_edges.setStyleSheet('')
        else:
            self.but_detect_edges.setStyleSheet(self.button_html_style)

    def but_use_polypaint_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_use_polypaint):
            self.but_use_polypaint.setStyleSheet('')
        else:
            self.but_use_polypaint.setStyleSheet(self.button_html_style)

    def but_half_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_half):
            self.but_half.setStyleSheet('')
            self.spin_quad_count.setEnabled(True)
            self.slider_quad_count.setEnabled(True)
        else:
            self.but_half.setStyleSheet(self.button_html_style)
            self.but_double.setStyleSheet('')
            self.but_same.setStyleSheet('')
            self.spin_quad_count.setEnabled(False)
            self.slider_quad_count.setEnabled(False)

    def but_same_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_same):
            self.but_same.setStyleSheet('')
            self.spin_quad_count.setEnabled(True)
            self.slider_quad_count.setEnabled(True)
        else:
            self.but_same.setStyleSheet(self.button_html_style)
            self.but_double.setStyleSheet('')
            self.but_half.setStyleSheet('')
            self.spin_quad_count.setEnabled(False)
            self.slider_quad_count.setEnabled(False)

    def but_double_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_double):
            self.but_double.setStyleSheet('')
            self.spin_quad_count.setEnabled(True)
            self.slider_quad_count.setEnabled(True)
        else:
            self.but_double.setStyleSheet(self.button_html_style)
            self.but_same.setStyleSheet('')
            self.but_half.setStyleSheet('')
            self.spin_quad_count.setEnabled(False)
            self.slider_quad_count.setEnabled(False)

    def but_adapt_clicked(self):
        """Style button when clicked."""

        if self.is_button_customized(self.but_adapt):
            self.but_adapt.setStyleSheet('')
        else:
            self.but_adapt.setStyleSheet(self.button_html_style)

    def write_line(self,
                   target: TextIOWrapper,
                   data_to_write: str
                   ) -> None:
        """Helper function. Write the given data to next line of the open file."""
        target.write(data_to_write)
        target.write('\n')

    def write_mesh_ply(self,
                   geometry_name: Union[str, om2.MFnMesh],
                   output_path: Path(),
                   stop_if_no_color_data: bool = True,
                   ) -> bool:
        """Export the mesh to .ply. Zbrush can get the color data from format.


        Args:
            geometry_name (Union[str, om2.MFnMesh]): The name of the geometry to check. 
            output_path (Path): the path the .ply mesh file.
            stop_if_no_color_data (bool, optional): allow to stop if no color. Defaults to True.

        Returns:
            bool: return if the mesh has color data or not.
        """
        if isinstance(geometry_name, str):
            selection_list = om2.MSelectionList()
            selection_list.add(geometry_name)
            mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))

        elif isinstance(geometry_name, om2.MFnMesh):
            mfn_mesh = geometry_name

        # Get the color data from the vertices
        mesh_has_color_data = False
        try:
            vertex_colors = mfn_mesh.getVertexColors()
            mesh_has_color_data = True
        except RuntimeError:
            pass

        if stop_if_no_color_data:
            if mesh_has_color_data is False:
                if output_path.is_file():
                    os.remove(output_path)
                return False

        file_ply = open(output_path, 'w', encoding='utf-8')
        file_ply.write('')

        # Get the positions of the vertices
        vertex_positions = mfn_mesh.getPoints(om2.MSpace.kWorld)
        u_coords, v_coords = mfn_mesh.getUVs()

        self.write_line(file_ply, "ply")
        self.write_line(file_ply, "format ascii 1.0")
        self.write_line(file_ply, "comment PLY export from ZR4M")
        self.write_line(file_ply, "comment file_ply name")
        self.write_line(file_ply, f"element vertex {mfn_mesh.numVertices}")
        self.write_line(file_ply, "property float x")
        self.write_line(file_ply, "property float y")
        self.write_line(file_ply, "property float z")
        self.write_line(file_ply, "property float s")
        self.write_line(file_ply, "property float t")
        self.write_line(file_ply, "property uchar red")
        self.write_line(file_ply, "property uchar green")
        self.write_line(file_ply, "property uchar blue")
        self.write_line(file_ply, f"element face {mfn_mesh.numPolygons}")
        self.write_line(file_ply, "property list uchar int vertex_indices")
        self.write_line(file_ply, "end_header")

        # Iterate over the vertex colors and positions and print the data
        for (color, position, u, v) in zip(vertex_colors, vertex_positions,u_coords,v_coords):
            red = abs(int(color[0] * 255))
            green = abs(int(color[1] * 255))
            blue = abs(int(color[2] * 255))
            x, y, z, _ = position  # Ignore the fourth component
            self.write_line(file_ply, f"{x} {y} {z} {u} {v} {red} {green} {blue}")

        # Iterate over the faces and write_line the vertex IDs and vertex counts
        for face_index in range(mfn_mesh.numPolygons):
            face_vertex = mfn_mesh.getPolygonVertices(face_index)
            face_vertex_count = len(face_vertex)
            vertex_ids = " ".join(map(str, face_vertex))
            self.write_line(file_ply, f"{face_vertex_count} {vertex_ids}")
        file_ply.close()
        return True

    def do_zremesh(self):
        """Do the following operations:
        - Write the zremesh settings to the the TMP directory.
        - For every material assigned to the selected mesh move every UV point to a different UDIM.
        - Triangulate every faces with more that four vertexes.
        - Export the resulting mesh to the TMP directory as Maya Ascii file. (Keep the creasing)
        """
        cmds.undoInfo(openChunk=True)
        try:
            # write zremesh setting to a txt using the zscript language
            file = open(self.zremesh_settings, 'w', encoding='utf-8')
            file.write('')

            self.write_line(file, '// set variable')
            if self.is_goz_installed:
                self.write_line(file, f'[VarDef, main_geo, "{self.output_maya_goz}" ]')
            else:
                self.write_line(file, f'[VarDef, main_geo, "{self.output_maya_ascii}" ]')
            self.write_line(
                file, f'[VarDef, output_zbrush, "{self.output_zbrush}" ]')
            self.write_line(file,
                            f'[VarDef, dummy_geo_retry_disabled,"{self.dummy_geo_retry_disabled}"]')
            self.write_line(file,
                            f'[VarDef, dummy_geo_zremesh_failed,"{self.dummy_geo_zremesh_failed}"]')
            self.write_line(
                file, f'[VarDef, dummy_geo_zremesh_done, "{self.dummy_geo_zremesh_done}" ]')
            self.write_line(file, '//')
            self.write_line(file, '')

            self.write_line(file, '[If,1,')
            self.write_line(file, '')
            self.write_line(file, '// hide zbrush')
            self.write_line(file, '[IPress,Restore]')
            self.write_line(file, '[IPress,Restore] // need to press twice')
            self.write_line(file, '[IPress,Hide]')
            self.write_line(file, '//')
            self.write_line(file, '')

            if self.sender() == self.but_zremesh:

                self.write_line(
                    file, '// if a tool is open delete it. Avoid cluttering Zbrush')
                self.write_line(file, '[If,[IExists,"Tool:SubTool:Del All"],')
                self.write_line(
                    file, '    [IKeyPress,"2",[IPress,"Tool:SubTool:Del All"]]')
                self.write_line(file, ']')
                self.write_line(file, '//')
                self.write_line(file, '')

                self.write_line(file, '// setup canvas and import geo')
                self.write_line(file, '[MVarDef,ZSM_MBlock,1]')
                self.write_line(file, '[IPress,Tool:PolyMesh3D]')
                self.write_line(file, '//if not duplicate .GoZ is imported flip the first time')
                self.write_line(file, '[IPress,"Tool:SubTool:Duplicate"]')
                self.write_line(file, '[FileNameSetNext, main_geo]')
                self.write_line(file, '[IPress, TOOL:Import:Import]')
                self.write_line(file, '[IKeyPress,"2",[IPress,"Tool:SubTool:Del Other"]]')
                self.write_line(file, '//[IPress,Tool:Display Properties:Double]')
                self.write_line(file, (
                    '[CanvasStroke,(ZObjStrokeV02n11=H3B6V14BH3B6V14CH3B6V14DH3B6V'
                    '14EH3B6V150H3B6V151YH3B6V151K1XH3B6V152H3B6V152H3B6V153H3B6V153)]'
                ))
                self.write_line(file,
                                '[TransformSet, (Document:Width*.5), (Document:Height*.5), 0, '
                                '100, 100, 100, 0, 0, 0]')
                self.write_line(file, '[IPress,Transform: Edit]')
                self.write_line(
                    file, '[IUnPress,Preferences:Lightbox:Open At Launch]')
                self.write_line(file, '//')
                self.write_line(file, '')

                self.write_line(
                    file, '// before zremesh give every udim a polygroup')
                self.write_line(file, '[IPress,Tool:Polygroups:Uv Groups]')
                self.write_line(file, '//')
                self.write_line(file, '')

            if self.sender() == self.but_retry:

                self.write_line(
                    file, '// if retry zremesh is not possible than export just the dummy geo')
                self.write_line(file, '[If,[IExists,"Tool:Geometry:Retry"],')
                self.write_line(file, '    //[Note,"Retry exists"')
                self.write_line(
                    file, '    [If,[IsEnabled,"Tool:Geometry:Retry"],')
                self.write_line(
                    file, '        //[Note, "Exists and is enabled"]')
                self.write_line(file, '        ,')
                self.write_line(
                    file, '        //[Note, "Exists and is not enabled"]')
                self.write_line(file, '        [IPress,Tool:PolyMesh3D]')
                self.write_line(
                    file, '        [FileNameSetNext, dummy_geo_retry_disabled ]')
                self.write_line(file, '        [IPress,Tool:Export]')
                self.write_line(file, '        [Exit]')
                self.write_line(file, '    ]')
                self.write_line(file, '    ,')
                self.write_line(file, '    [IPress,Tool:PolyMesh3D]')
                self.write_line(
                    file, '    [FileNameSetNext, dummy_geo_retry_disabled ]')
                self.write_line(file, '    [IPress,Tool:Export]')
                self.write_line(file, '    //[Note, "Retry not exists"]')
                self.write_line(file, '    [Exit]')
                self.write_line(file, ']')
                self.write_line(file, '//')
                self.write_line(file, '')

            self.write_line(file, '// tune zremesh settings')

            if self.is_button_customized(self.but_keep_creases) is True:
                self.write_line(file, '[IPress,Tool:Geometry:KeepCreases]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:KeepCreases]')

            if self.is_button_customized(self.but_detect_edges) is True:
                self.write_line(file, '[IPress,Tool:Geometry:DetectEdges]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:DetectEdges]')

            if self.is_button_customized(self.but_freeze_groups) is True:
                self.write_line(file, '[IPress,Tool:Geometry:FreezeGroups]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:FreezeGroups]')

            self.write_line(file, '[ISet,Tool:Geometry:SmoothGroups,'
                            f'{str(self.spin_smooth_groups.value())}]')

            if self.is_button_customized(self.but_keep_groups) is True:
                self.write_line(file, '[IPress,Tool:Geometry:KeepGroups]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:KeepGroups]')

            if self.is_button_customized(self.but_freeze_border) is True:
                self.write_line(file, '[IPress,Tool:Geometry:FreezeBorder]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:FreezeBorder]')

            if self.is_button_customized(self.but_use_polypaint) is True:
                self.write_line(file, '[IPress,Tool:Geometry:Use Polypaint]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:Use Polypaint]')

            self.write_line(
                file, f'[ISet,Tool:Geometry:ColorDensity,{str(self.spin_color_density.value())}]')

            self.write_line(file,
                            '[ISet,Tool:Geometry:Target Polygons Count,'
                            f'{str(self.spin_quad_count.value())}]')

            if self.is_button_customized(self.but_half) is True:
                self.write_line(file, '[IPress,Tool:Geometry:Half]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:Half]')

            if self.is_button_customized(self.but_same) is True:
                self.write_line(file, '[IPress,Tool:Geometry:Same]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:Same]')

            if self.is_button_customized(self.but_double) is True:
                self.write_line(file, '[IPress,Tool:Geometry:Double]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:Double]')

            if self.is_button_customized(self.but_adapt) is True:
                self.write_line(file, '[IPress,Tool:Geometry:Adapt]')
            else:
                self.write_line(file, '[IUnPress,Tool:Geometry:Adapt]')

            self.write_line(
                file, f'[ISet,Tool:Geometry:AdaptiveSize,{str(self.slider_adaptive_size.value())}]')

            if self.symmetry_x.isChecked() is True:
                self.write_line(file, '[IPress,Transform:>X<]')
            else:
                self.write_line(file, '[IUnPress,Transform:>X<]')

            if self.symmetry_y.isChecked() is True:
                self.write_line(file, '[IPress,Transform:>Y<]')
            else:
                self.write_line(file, '[IUnPress,Transform:>Y<]')

            if self.symmetry_z.isChecked() is True:
                self.write_line(file, '[IPress,Transform:>Z<]')
            else:
                self.write_line(file, '[IUnPress,Transform:>Z<]')

            if self.symmetry_r.isChecked() is True:
                self.write_line(file, '[IPress,Transform:(R)]')
                self.write_line(
                    file, f'[ISet,Transform:RadialCount,{str(self.spin_radial_sym.value())}]')
            else:
                self.write_line(file, '[IUnPress,Transform:(R)]')

            if any(
                [self.symmetry_x.isChecked(),
                 self.symmetry_y.isChecked(),
                 self.symmetry_z.isChecked(),
                 self.symmetry_r.isChecked()]
            ):
                self.write_line(file, '[IPress,Transform:Activate Symmetry]')
                self.write_line(file, '[IPress,Transform:LSym]')
                #self.write_line(file, '[IModSet,Transform:LSym,1]')
            else:
                self.write_line(file, '[IUnPress,Transform:Activate Symmetry]')
                self.write_line(file, '[IUnPress,Transform:LSym]')
                #self.write_line(file, '[IModSet,Transform:LSym,0]')
            self.write_line(file, '//')
            self.write_line(file, '')

            self.write_line(file, '// do zremesher and export')
            self.write_line(file, '[IPress, Edit:Tool:DelOlderUH]')
            if self.sender() == self.but_zremesh:
                self.write_line(file, '[IPress,Tool:Geometry:ZRemesher]')
            elif self.sender() == self.but_retry:
                self.write_line(
                    file, '[IKeyPress,"3",[IPress,Tool:Geometry:Retry]]')

            self.write_line(file, '[If,[IsEnabled,Edit:Tool:Undo],')
            self.write_line(file, '     //[Note, "zremesh done"]')
            self.write_line(file, '     [FileNameSetNext, output_zbrush]')
            self.write_line(file, '     [IPress,Tool:Export]')
            self.write_line(
                file, '     [VarSet, CurrentSubtoolIndex,[IGet,Tool:ItemInfo]]')
            self.write_line(file, '     [IPress,Tool:PolyMesh3D]')
            self.write_line(
                file, '     [FileNameSetNext, dummy_geo_zremesh_done]')
            self.write_line(file, '     [IPress,Tool:Export]')
            self.write_line(
                file, '     [ISet, Tool:ItemInfo,  CurrentSubtoolIndex]')
            self.write_line(file, '     ,')
            self.write_line(file, '     //[Note, "zremesh failed"]')
            self.write_line(
                file, '     [VarSet, CurrentSubtoolIndex,[IGet,Tool:ItemInfo]]')
            self.write_line(file, '     [IPress,Tool:PolyMesh3D]')
            self.write_line(
                file, '     [FileNameSetNext, dummy_geo_zremesh_failed]')
            self.write_line(file, '     [IPress,Tool:Export]')
            self.write_line(
                file, '     [ISet, Tool:ItemInfo,  CurrentSubtoolIndex]')
            self.write_line(file, ']')
            self.write_line(file, '//')
            self.write_line(file, '')
            self.write_line(file, ']')

            file.close()
            ###

            if self.sender() == self.but_zremesh:

                geometry_name = get_current_selected_mesh(
                    complain_if_more_selected=True, complain_if_none_selected=True)

                duplicate_geometry = duplicate_mesh_without_set(
                    geometry_name, name_duplicate="mesh_to_be_zremeshed")[0]
                cmds.makeIdentity(
                    duplicate_geometry,
                    apply=True, translate=True, rotate=True, scale=True, preserveNormals=True)

                selection_list = om2.MSelectionList()
                selection_list.add(geometry_name)
                selection_list.add(duplicate_geometry)

                mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))
                # cord points used later to check if the last output is equal to the previous one
                self.list_vtx_cord_geo_input_zremesh = mfn_mesh.getPoints()

                # triangulate nsided faces
                mfn_mesh = om2.MFnMesh(selection_list.getDagPath(1))

                num_faces = mfn_mesh.numPolygons
                list_nsided_faces_index = set()
                for i in range(num_faces):
                    num_vertexes = mfn_mesh.polygonVertexCount(i)
                    if num_vertexes > 4:
                        list_nsided_faces_index.add(i)

                list_nsided_faces_full_name = add_full_name_to_index_component(
                    input_index_component=list_nsided_faces_index,
                    geometry_name=duplicate_geometry,
                    mode="f")
                if len(list_nsided_faces_full_name):
                    cmds.polyTriangulate(list_nsided_faces_full_name, ch=False)

                # for every material create a new udim.
                # Zbrush will create a polygroup for each udim found.

                dict_faces_per_material = calculate_faces_per_material(
                    duplicate_geometry)

                if len(dict_faces_per_material.keys()) > 1:

                    counter_udim = 0
                    for material in dict_faces_per_material:
                        list_faces = dict_faces_per_material.get(material)
                        if list_faces:
                            cmds.polyForceUV(list_faces, unitize=True)
                            cmds.polyEditUV(list_faces, pivotU=0.5,
                                            pivotV=0.5, scaleU=0.9, scaleV=0.9)
                            cmds.polyEditUV(list_faces, u=0,
                                            v=counter_udim, r=1)
                            counter_udim += 1

                # export .ma or .GoZ. Prefer GoZ
                try:
                    vertex_colors = mfn_mesh.getVertexColors()
                    mesh_has_color_data = True
                except RuntimeError:
                    mesh_has_color_data = False

                if mesh_has_color_data:
                # make white the empty vertex. for zbrush white is none
                    list_vertex_to_flood = []
                    for i, color in enumerate(vertex_colors):
                        if any(num < 0 for num in [color[0], color[1], color[2]]):
                            list_vertex_to_flood.append(i)

                    if list_vertex_to_flood:
                        cmds.polyColorPerVertex(
                            add_full_name_to_index_component(
                                list_vertex_to_flood,duplicate_geometry,mode="vtx"),
                                r=1, g=1, b=1, a=1, colorDisplayOption=True)

                if self.is_goz_installed:
                    dummy_mat,dummy_SHA_GRP = create_material("dummy_mat")# goz chash if texture
                    cmds.sets(duplicate_geometry, forceElement=dummy_SHA_GRP)
                    cmds.select(duplicate_geometry)
                    cmds.gozMaya('-o', str(self.output_maya_goz)[:-4])
                    #cmds.gozMaya('-i', str(self.output_maya_goz)[:-4]) # Es. for import the .GoZ
                else:
                    if mesh_has_color_data:
                        cmds.polyColorPerVertex(duplicate_geometry, rem=True) #.ma dont handle color
                    cmds.select(duplicate_geometry)
                    cmds.file(self.output_maya_ascii, exportSelected=True,
                            force=True, typ="mayaAscii", options="v=0")

                cmds.delete(duplicate_geometry)
                if self.is_goz_installed:
                    cmds.delete(dummy_mat,dummy_SHA_GRP)

                self.uuid_last_maya_exported_geo = cmds.ls(
                    geometry_name, uuid=True)[0]
                self.name_last_maya_exported_geo = geometry_name

            self.but_zremesh.hide()
            self.but_retry.hide()
            self.but_abort_zremesh.show()

            self.start_wait_for_zremesh()

            self.but_pressed = self.sender()

            if self.dummy_geo_zremesh_done.is_file():
                os.remove(self.dummy_geo_zremesh_done)
            if self.dummy_geo_retry_disabled.is_file():
                os.remove(self.dummy_geo_retry_disabled)
            if self.dummy_geo_zremesh_failed.is_file():
                os.remove(self.dummy_geo_zremesh_failed)
            if self.output_zbrush.is_file():
                os.remove(self.output_zbrush)

            self.start_keep_focus()
            subprocess.Popen(
                [self.zbrush_exe_location, self.start_zbrush_bridge_zsc])
        finally:
            cmds.undoInfo(closeChunk=True)

    def keep_focus(self):
        """Create a dummy UI that keeps the focus on itself."""
        time_keep_focus = self.dict_timer_keep_focus.get("time_keep_focus")
        time_start = time.time()

        dummy_gui = cmds.window(title="dummy window very small and far away. Keep Maya in focus.",
                                widthHeight=[1, 1],
                                topLeftCorner=[0, 1000000],
                                toolbox=True)

        while time_keep_focus > (time.time() - time_start):
            cmds.showWindow(dummy_gui)
        cmds.deleteUI(dummy_gui, window=True)
        self.dict_timer_keep_focus["timer"].stop()

    def start_keep_focus(self):
        """Start the keep focus loop."""
        timer = QTimer()
        timer.timeout.connect(self.keep_focus)
        timer.start()

        # Store reference
        self.dict_timer_keep_focus = {}
        self.dict_timer_keep_focus["timer"] = timer
        second_to_keep_focus = 2
        self.dict_timer_keep_focus["time_keep_focus"] = second_to_keep_focus

    def stop_keep_focus(self):
        """Stop the keep focus loop."""

        try:
            self.dict_timer_keep_focus
        except AttributeError:
            pass
        else:
            if isinstance(self.dict_timer_keep_focus, QTimer):
                self.dict_timer_keep_focus["timer"].stop()

    def start_wait_for_zremesh(self):
        """Start to wait in background until the zremesh output is ready."""

        timer = QTimer()
        timer.setInterval(200)
        timer.timeout.connect(self.wait_for_zremesh)
        timer.start()

        # Store reference
        self.timer_wait_for_zremesh = {}
        self.timer_wait_for_zremesh = timer

    def stop_wait_for_zremesh(self):
        """Stop waiting for the zremesh output."""

        try:
            self.timer_wait_for_zremesh
        except AttributeError:
            pass
        else:
            if isinstance(self.timer_wait_for_zremesh, QTimer):
                self.timer_wait_for_zremesh.stop()

    def wait_for_zremesh(self):
        """Check if Zbrush has done processing.
        - If the dummy geo is exported than import the output mesh.
        - If dummy geo exists but not he output mesh than the retry was impossible.
        - Import the geo convert the set into materials with a random color.
        - If retry was pressed than try to delete the previously zremesh output.
        """

        if self.dummy_geo_retry_disabled.is_file():
            self.stop_wait_for_zremesh()
            self.but_abort_zremesh.hide()
            self.but_zremesh.show()
            self.but_retry.show()
            self.but_retry.setEnabled(False)
            message("Retry unavailable. Do a zremesh instead", raise_error=False)
            return

        if self.dummy_geo_zremesh_failed.is_file():
            self.stop_wait_for_zremesh()
            self.but_abort_zremesh.hide()
            self.but_zremesh.show()
            self.but_retry.show()
            message("Zremesh failed. Try changing settings", raise_error=False)
            return

        if self.dummy_geo_zremesh_done.is_file():
            self.stop_wait_for_zremesh()
            list_nodes_imported = cmds.file(
                self.output_zbrush,
                i=True,
                type="OBJ",
                returnNewNodes=True,
                ignoreVersion=True,
                namespace="zr",
                mergeNamespacesOnClash=True,
                options="mo=0;lo=1",
                prompt=False,
            )
            name_imported_mesh = set()
            name_shape_imported_mesh = set()
            for node in list_nodes_imported:
                if cmds.objectType(node, isType="mesh"):
                    name_shape_imported_mesh.add(node)
                if cmds.objectType(node, isType="transform"):
                    name_imported_mesh.add(node)

            name_shape_imported_mesh = name_shape_imported_mesh.pop()
            cmds.setAttr(f'{name_shape_imported_mesh}.displayColors', False)
            name_imported_mesh = name_imported_mesh.pop()
            name_imported_mesh = cmds.rename(
                name_imported_mesh, f"{self.name_last_maya_exported_geo.split('|')[-1] }_zr_01")
            name_shape_imported_mesh = cmds.listRelatives(name_imported_mesh, shapes=True)[0]

            if self.is_goz_installed:
                # the GoZ someting has the coordinates inverted
                cmds.setAttr(name_imported_mesh + ".scaleY", -1)
                cmds.setAttr(name_imported_mesh + ".scaleZ", -1)
                cmds.makeIdentity(
                    name_imported_mesh,
                    apply=True, translate=True, rotate=True, scale=True, preserveNormals=True)

            current_scene_unit = cmds.currentUnit(query=True, linear=True)
            if current_scene_unit == "cm":
                pass
            elif current_scene_unit == "mm":
                cmds.setAttr(f"{name_imported_mesh}.scaleX", 0.1)
                cmds.setAttr(f"{name_imported_mesh}.scaleY", 0.1)
                cmds.setAttr(f"{name_imported_mesh}.scaleZ", 0.1)
            elif current_scene_unit == "m":
                cmds.setAttr(f"{name_imported_mesh}.scaleX", 100)
                cmds.setAttr(f"{name_imported_mesh}.scaleY", 100)
                cmds.setAttr(f"{name_imported_mesh}.scaleZ", 100)
            elif current_scene_unit == "yd":
                cmds.setAttr(f"{name_imported_mesh}.scaleX", 91.44)
                cmds.setAttr(f"{name_imported_mesh}.scaleY", 91.44)
                cmds.setAttr(f"{name_imported_mesh}.scaleZ", 91.44)
            elif current_scene_unit == "ft":
                cmds.setAttr(f"{name_imported_mesh}.scaleX", 30.48)
                cmds.setAttr(f"{name_imported_mesh}.scaleY", 30.48)
                cmds.setAttr(f"{name_imported_mesh}.scaleZ", 30.48)
            elif current_scene_unit == "in":
                cmds.setAttr(f"{name_imported_mesh}.scaleX", 2.54)
                cmds.setAttr(f"{name_imported_mesh}.scaleY", 2.54)
                cmds.setAttr(f"{name_imported_mesh}.scaleZ", 2.54)
            cmds.makeIdentity(
                name_imported_mesh,
                apply=True, translate=True, rotate=True, scale=True, preserveNormals=True)

            # read quick-set and assign lambert with random color for each
            list_quick_set_import_geometry = (
                set(cmds.listSets(object=name_imported_mesh, extendToShape=1))
                - set(cmds.listSets(object=name_imported_mesh,
                      extendToShape=1, type=1))
            )
            counter_polygroup = 1
            if len(list_quick_set_import_geometry) > 1:
                for name_quick_set in list_quick_set_import_geometry:
                    face_polygroup = cmds.sets(name_quick_set, q=True)
                    polygroup_name = f"polygroup_ZR4M_{str(counter_polygroup).zfill(4)}"

                    if not cmds.objExists(polygroup_name):
                        material_name = create_material(polygroup_name)[0]
                        cmds.setAttr(
                            material_name + ".color", random(), random(), random(), type="double3")

                    name_shading_group = cmds.listConnections(
                        polygroup_name, type="shadingEngine")[0]
                    cmds.sets(face_polygroup, forceElement=name_shading_group)
                    counter_polygroup += 1
            cmds.delete(list_quick_set_import_geometry)

            exist_last_maya_exported_geo = False
            exist_last_zremesh_output = False
            list_obj_in_scene = cmds.ls(flatten=True)
            for obj in list_obj_in_scene:
                uuid_current_obj = cmds.ls(obj, uuid=True)[0]
                if self.uuid_object_last_zremesh_output == uuid_current_obj:
                    name_old_zremesh_output = obj
                    exist_last_zremesh_output = True
                if self.uuid_last_maya_exported_geo == uuid_current_obj:
                    name_last_maya_exported_geo = obj
                    exist_last_maya_exported_geo = True
                if exist_last_maya_exported_geo and exist_last_zremesh_output:
                    break

            if self.but_pressed == self.but_zremesh:
                if are_two_meshes_identical(
                    self.list_vtx_cord_geo_input_zremesh,
                    name_imported_mesh
                ):

                    cmds.delete(name_imported_mesh)
                    self.but_abort_zremesh.hide()
                    self.but_zremesh.show()
                    self.but_retry.show()
                    message("The output mesh is identical", raise_error=False)
                    return
            if self.but_pressed == self.but_retry:
                if are_two_meshes_identical(
                    self.list_vtx_cord_geo_last_output_zremesh,
                    name_imported_mesh
                ):

                    cmds.delete(name_imported_mesh)
                    self.but_abort_zremesh.hide()
                    self.but_zremesh.show()
                    self.but_retry.show()
                    message("The retry output mesh is identical",
                            raise_error=False)
                    return

            if exist_last_zremesh_output:
                if self.but_pressed == self.but_retry:
                    cmds.transferAttributes(name_last_maya_exported_geo, name_imported_mesh,
                                            transferPositions=0, transferNormals=0, transferUVs=2,
                                            transferColors=2, sampleSpace=0, sourceUvSpace="map1",
                                            targetUvSpace="map1", searchMethod=3, flipUVs=0,
                                            colorBorders=1)
                    cmds.delete(name_imported_mesh, constructionHistory=True)
                    cmds.delete(name_old_zremesh_output)
                    cmds.setAttr(name_shape_imported_mesh + '.displayColors', 0)
            if exist_last_maya_exported_geo:
                if self.but_pressed == self.but_zremesh:
                    cmds.setAttr(
                        f"{name_last_maya_exported_geo}.visibility", 0)
                    cmds.transferAttributes(name_last_maya_exported_geo, name_imported_mesh,
                                            transferPositions=0, transferNormals=0, transferUVs=2,
                                            transferColors=2, sampleSpace=0, sourceUvSpace="map1",
                                            targetUvSpace="map1", searchMethod=3, flipUVs=0,
                                            colorBorders=1)
                    cmds.delete(name_imported_mesh, constructionHistory=True)
                    cmds.setAttr(name_shape_imported_mesh + '.displayColors', 0)

            cmds.select(name_imported_mesh)

            selection_list = om2.MSelectionList()
            selection_list.add(name_imported_mesh)
            mfn_mesh = om2.MFnMesh(selection_list.getDagPath(0))
            self.list_vtx_cord_geo_last_output_zremesh = mfn_mesh.getPoints()

            self.uuid_object_last_zremesh_output = cmds.ls(
                name_imported_mesh, uuid=True)[0]

            self.but_abort_zremesh.hide()
            self.but_zremesh.show()
            self.but_retry.show()
            self.but_retry.setEnabled(True)

            # cmds.file() break the undo so it is better to flush everything
            cmds.flushUndo()

    def but_abort_zremesh_clicked(self):
        """Stop wait for the zremesh output."""
        self.stop_wait_for_zremesh()
        self.but_abort_zremesh.hide()
        self.but_zremesh.show()
        self.but_retry.show()

    # garment utilities
    def set_flat_ref_geo(self, name_flat_ref_geo: str):
        """Set the flat ref geometry."""
        cmds.delete(name_flat_ref_geo, constructionHistory=True)
        cmds.makeIdentity(name_flat_ref_geo, apply=True,
                          translate=True, rotate=True, scale=True, preserveNormals=True)
        raise_error_if_mesh_has_missing_uvs(name_flat_ref_geo)
        raise_error_if_mesh_has_one_uv_shell(name_flat_ref_geo)
        raise_error_if_mesh_has_overlapping_uvs(name_flat_ref_geo)
        raise_error_if_mesh_is_unflat(name_flat_ref_geo)

        self.full_name_flat_ref_geo = name_flat_ref_geo
        if "|" in name_flat_ref_geo:
            name_flat_ref_geo = name_flat_ref_geo.split('|')[-1]

        self.label_flat.setText(
            f'''<p><strong>Flatten ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>
                <font color="Dark Orange">{name_flat_ref_geo}</font></p>''')
        self.but_uv_from_flat_ref.setEnabled(True)

        if isinstance(self.job_check_existences_flat_ref, int):
            if cmds.scriptJob(exists=self.job_check_existences_flat_ref):
                cmds.scriptJob(kill=self.job_check_existences_flat_ref)

        self.job_check_existences_flat_ref = cmds.scriptJob(
            event=["SelectionChanged", self.check_status_flat_ref_geo])

        if self.full_name_flat_ref_geo and self.full_name_posed_ref_geo:
            self.but_relax_flat.setEnabled(True)
            self.but_create_mirror.setEnabled(True)
            self.but_reconstruct_mesh.setEnabled(True)
            self.but_rebind_label.setEnabled(True)

    def but_set_flat_ref_geo_clicked(self):
        """Set the flat ref geometry."""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            name_flat_ref_geo = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            self.set_flat_ref_geo(name_flat_ref_geo)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def check_status_flat_ref_geo(self):
        """If the flat ref geometry changes name or is deleted disable functionality."""
        if self.full_name_flat_ref_geo:
            if not cmds.objExists(self.full_name_flat_ref_geo):
                self.full_name_flat_ref_geo = None
                self.but_uv_from_flat_ref.setEnabled(False)
                self.but_relax_flat.setEnabled(False)
                self.but_create_mirror.setEnabled(False)
                self.but_reconstruct_mesh.setEnabled(False)

                if not [curve for curve in return_curve_in_scene()[0]
                        if cmds.attributeQuery("connection_between_indicator_and_label_curve",
                                               node=curve, exists=True)]:
                    self.but_toggle_label.setEnabled(False)
                    self.but_rebind_label.setEnabled(False)

                self.label_flat.setText(
                    '<p><strong>Flatten ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>'
                    '<font color="red">No geometry found</font></p>'
                )

    def set_posed_ref_geo(self, name_posed_ref_geo: str):
        """Set the posed ref geometry."""

        cmds.delete(name_posed_ref_geo, constructionHistory=True)
        cmds.makeIdentity(name_posed_ref_geo, apply=True,
                          translate=True, rotate=True, scale=True, preserveNormals=True)
        raise_error_if_mesh_has_missing_uvs(name_posed_ref_geo)
        raise_error_if_mesh_has_one_uv_shell(name_posed_ref_geo)
        raise_error_if_mesh_has_overlapping_uvs(name_posed_ref_geo)
        raise_error_if_mesh_has_unpairable_uv_border(name_posed_ref_geo)

        self.full_name_posed_ref_geo = name_posed_ref_geo
        if "|" in name_posed_ref_geo:
            name_posed_ref_geo = name_posed_ref_geo.split('|')[-1]

        self.but_position_from_posed_ref.setEnabled(True)
        self.label_posed.setText(
            f'''<p><strong>Posed ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>
                <font color="Dark Orange">{name_posed_ref_geo}</font></p>''')

        if isinstance(self.job_check_existences_posed_ref, int):
            if cmds.scriptJob(exists=self.job_check_existences_posed_ref):
                cmds.scriptJob(kill=self.job_check_existences_posed_ref)

        self.job_check_existences_posed_ref = cmds.scriptJob(
            event=["SelectionChanged", self.check_status_posed_ref_geo])

        if self.full_name_flat_ref_geo and self.full_name_posed_ref_geo:
            self.but_relax_flat.setEnabled(True)
            self.but_create_mirror.setEnabled(True)
            self.but_reconstruct_mesh.setEnabled(True)
            self.but_rebind_label.setEnabled(True)

    def but_set_posed_ref_geo_clicked(self):
        """Set the posed ref geometry."""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            name_posed_ref_geo = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            self.set_posed_ref_geo(name_posed_ref_geo)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def check_status_posed_ref_geo(self):
        """If the posed ref geometry changes name or is deleted disable functionality."""
        if self.full_name_posed_ref_geo:
            if not cmds.objExists(self.full_name_posed_ref_geo):
                self.full_name_posed_ref_geo = None
                self.but_position_from_posed_ref.setEnabled(False)
                self.but_relax_flat.setEnabled(False)
                self.but_toggle_label.setEnabled(False)
                self.but_create_mirror.setEnabled(False)
                self.but_reconstruct_mesh.setEnabled(False)

                if not [curve for curve in return_curve_in_scene()[0]
                        if cmds.attributeQuery("connection_between_indicator_and_label_curve",
                                               node=curve, exists=True)]:
                    self.but_toggle_label.setEnabled(False)
                    self.but_rebind_label.setEnabled(False)

                self.label_posed.setText(
                    '<p><strong>Posed ref:&nbsp;&nbsp;&nbsp;&nbsp;</strong>'
                    '<font color="red">No geometry found</font></p>'
                )

    def but_uv_from_flat_ref_geo_clicked(self):
        """Transfer the UV to the selected mesh from the flatten geometry reference."""
        cmds.undoInfo(openChunk=True)
        try:
            selected_geometry = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            if self.full_name_flat_ref_geo:
                if selected_geometry != self.full_name_flat_ref_geo:
                    cmds.delete(selected_geometry, constructionHistory=True)
                    cmds.transferAttributes(self.full_name_flat_ref_geo, selected_geometry,
                                            transferPositions=0, transferNormals=0, transferUVs=2,
                                            transferColors=2, sampleSpace=0, sourceUvSpace="map1",
                                            targetUvSpace="map1", searchMethod=3, flipUVs=0,
                                            colorBorders=1)
                    cmds.delete(selected_geometry, constructionHistory=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    def but_position_from_posed_ref_geo_clicked(self):
        """Transfer the position of the selected mesh to the posed geometry reference."""
        cmds.undoInfo(openChunk=True)
        try:
            selected_geometry = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            if self.full_name_posed_ref_geo:
                if selected_geometry != self.full_name_posed_ref_geo:
                    cmds.delete(selected_geometry, constructionHistory=True)
                    cmds.transferAttributes(self.full_name_posed_ref_geo, selected_geometry,
                                            transferPositions=1, transferNormals=0, transferUVs=2,
                                            transferColors=2, sampleSpace=3, sourceUvSpace="map1",
                                            targetUvSpace="map1", searchMethod=3, flipUVs=0,
                                            colorBorders=1)
                    cmds.delete(selected_geometry, constructionHistory=True)
        finally:
            cmds.undoInfo(closeChunk=True)

    def but_relax_flat_geo_clicked(self):
        """Relax the selected flatten geometry using the existing perimeter curves."""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            flatten_mesh_to_relax = get_current_selected_mesh(
                complain_if_more_selected=False, complain_if_none_selected=True)
            relax_flat_mesh(
                flatten_mesh_to_relax, self.full_name_posed_ref_geo, self.full_name_flat_ref_geo)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def but_unwrap_clicked(self):
        """Unwrap the selected mesh"""
        cmds.undoInfo(openChunk=True)
        try:
            mesh_to_unwrap = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            cmds.delete(mesh_to_unwrap, constructionHistory=True)
            cmds.makeIdentity(mesh_to_unwrap, apply=True,
                              translate=True, rotate=True, scale=True, preserveNormals=True)
            raise_error_if_mesh_has_missing_uvs(mesh_to_unwrap)
            raise_error_if_mesh_has_overlapping_uvs(mesh_to_unwrap)

            list_component_on_uv_border = get_component_on_border(
                mesh_to_unwrap, mode="UV")
            list_vertex_on_uv_border_index = list_component_on_uv_border[1]
            unwrap(mesh_to_unwrap, list_vertex_on_uv_border_index)
        finally:
            cmds.undoInfo(closeChunk=True)

    def but_unwrap_and_analyze_clicked(self):
        """Unwrap and create the curves paring"""

        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            mesh_to_unwrap_and_analyze = get_current_selected_mesh(
                complain_if_more_selected=True, complain_if_none_selected=True)
            output_analyze = analyze_and_unwrap(mesh_to_unwrap_and_analyze)
            self.set_flat_ref_geo(output_analyze[0])
            self.set_posed_ref_geo(mesh_to_unwrap_and_analyze)
            # this is the only function that can create label so make sense to check here
            if [curve for curve in return_curve_in_scene()[0]
                    if cmds.attributeQuery("connection_between_indicator_and_label_curve",
                                           node=curve, exists=True)]:
                self.but_rebind_label.setEnabled(True)
                self.but_toggle_label.setEnabled(True)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def but_create_mirror_clicked(self):
        """Create a mirror if the two unflatten shapes are the same."""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            list_mesh_to_mirror = get_current_selected_mesh(
                complain_if_more_selected=False, complain_if_none_selected=True)
            list_couple_paired = create_and_place_mirror(
                list_mesh_to_mirror, self.full_name_flat_ref_geo)[0]

            dict_cord_master_uv_point = dict_cord_master_uv_points_from_posed_mesh(
                self.full_name_posed_ref_geo)
            list_master_mesh = {couple[0] for couple in list_couple_paired}
            list_slave_mesh = {couple[1] for couple in list_couple_paired}

            # rebind automatically the two meshes. Optional but practical.
            bind_label_indicator(list_input_geometry=list_master_mesh,
                                 bool_create_label=False,
                                 bool_unhide_updated_label=True,
                                 bool_just_return_found_label_curve=False,
                                 bool_check_if_proper_input=True,
                                 posed_ref_geometry=None,
                                 dict_cord_master_uv_point=dict_cord_master_uv_point,
                                 list_perimeter_curve=None)
            bind_label_indicator(list_input_geometry=list_slave_mesh,
                                 bool_create_label=False,
                                 bool_unhide_updated_label=False,
                                 bool_just_return_found_label_curve=False,
                                 bool_check_if_proper_input=True,
                                 posed_ref_geometry=None,
                                 dict_cord_master_uv_point=dict_cord_master_uv_point,
                                 list_perimeter_curve=None)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def but_rebind_label_clicked(self):
        """Rebind the label indicator to the current mesh selection"""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            list_label_curve = set()
            for curve in return_curve_in_scene()[0]:
                if cmds.attributeQuery("connection_between_float_indicator_and_label_curve",
                                       node=curve, exists=True):
                    list_label_curve.add(curve)
            if not list_label_curve:
                self.but_toggle_label.setEnabled(False)
                self.but_rebind_label.setEnabled(False)
                return

            list_mesh_to_update_label = get_current_selected_mesh(
                complain_if_more_selected=False, complain_if_none_selected=True)
            bind_label_indicator(list_input_geometry=list_mesh_to_update_label,
                                 bool_create_label=False,
                                 bool_unhide_updated_label=True,
                                 bool_just_return_found_label_curve=False,
                                 bool_check_if_proper_input=True,
                                 posed_ref_geometry=self.full_name_posed_ref_geo,
                                 dict_cord_master_uv_point=None,
                                 list_perimeter_curve=None)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def but_toggle_label_clicked(self):
        """Hide/show the labels curves"""

        # if nothing is selected than process every curve.
        # else if some mesh is selected and the posed ref geometry is linked than
        # process only the label that are closest to the selected mesh.

        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            list_mesh_selected = get_current_selected_mesh(
                complain_if_more_selected=False, complain_if_none_selected=False)
            if list_mesh_selected and self.full_name_posed_ref_geo:
                list_label_curve = bind_label_indicator(
                    list_input_geometry=list_mesh_selected,
                    bool_create_label=False,
                    bool_unhide_updated_label=False,
                    bool_just_return_found_label_curve=True,
                    bool_check_if_proper_input=True,
                    posed_ref_geometry=self.full_name_posed_ref_geo,
                    dict_cord_master_uv_point=None,
                    list_perimeter_curve=None)
            else:
                list_label_curve = set()
                for curve in return_curve_in_scene()[0]:
                    if cmds.attributeQuery("connection_between_float_indicator_and_label_curve",
                                           node=curve, exists=True):
                        list_label_curve.add(curve)

            if not list_label_curve:
                self.but_toggle_label.setEnabled(False)
                self.but_rebind_label.setEnabled(False)
                return

            status_visibility_label_curve = set()
            for curve in list_label_curve:
                status_visibility_label_curve.add(
                    cmds.getAttr(f"{curve}.visibility"))
            if {True} == status_visibility_label_curve:
                need_to_hide = True
            elif {False} == status_visibility_label_curve:
                need_to_hide = False
            else:
                need_to_hide = True
            if need_to_hide:
                for label_curve in list_label_curve:
                    cmds.setAttr(f"{label_curve}.visibility", 0)
            else:
                for label_curve in list_label_curve:
                    cmds.setAttr(f"{label_curve}.visibility", 1)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def but_reconstruct_mesh_clicked(self):
        """Reconstruct the mesh when finished with the retopo."""
        cmds.undoInfo(openChunk=True)
        current_scene_unit = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear='cm')
        try:
            list_mesh_selected = get_current_selected_mesh(
                complain_if_more_selected=False, complain_if_none_selected=True)
            mesh_merged = reconstruct_mesh(
                list_mesh_selected,
                self.full_name_posed_ref_geo,
                self.full_name_flat_ref_geo)
            isolated_panel = cmds.paneLayout('viewPanes', q=True, pane1=True)
            cmds.isolateSelect(isolated_panel, state=False)
            cmds.isolateSelect(isolated_panel, state=True)
            cmds.isolateSelect(isolated_panel, addDagObject=mesh_merged)
            cmds.displaySmoothness(mesh_merged, divisionsU=3, divisionsV=3,
                                   pointsWire=16, pointsShaded=4, polygonObject=3)
            cmds.select(mesh_merged)
        finally:
            cmds.currentUnit(linear=current_scene_unit)
            cmds.undoInfo(closeChunk=True)

    def evaluate_mouse(self):
        """Evaluate the mouse position."""
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            position = QCursor.pos()
            shape_node_object_found = self.get_object_under_cursor()
            if self.data_mouse_tracker.get("previous_object_found") is not None:
                if self.data_mouse_tracker["previous_object_found"] != shape_node_object_found:
                    last_pointer_curve_showed = self.data_mouse_tracker.get(
                        "last_curve_showed")
                    if cmds.objExists(last_pointer_curve_showed):
                        cmds.setAttr(last_pointer_curve_showed +
                                     ".visibility", 0)

            if position == self.data_mouse_tracker.get("lastPosition"):
                if not cmds.nodeType(shape_node_object_found) == "nurbsSurface":
                    return
                nurbs_full_path = cmds.listRelatives(
                    shape_node_object_found, path=True, parent=True)[0]
                full_name_curve_perimeter = cmds.listRelatives(
                    nurbs_full_path, parent=True, path=True)[0]
                nurbs_full_path = cmds.listRelatives(
                    shape_node_object_found, path=True, parent=True)[0]
                full_name_curve_perimeter = cmds.listRelatives(
                    nurbs_full_path, parent=True, path=True)[0]
                pointer_curve = cmds.listConnections(
                    f"{full_name_curve_perimeter}.connection_between_perimeter_and_pointer_curve")
                if pointer_curve:
                    pointer_curve = pointer_curve.pop()
                    cmds.setAttr(pointer_curve + ".visibility", 1)
                    self.data_mouse_tracker["last_curve_showed"] = pointer_curve
                    self.data_mouse_tracker["previous_object_found"] = shape_node_object_found
                else:
                    tooltip = QLabel("Not sewed")
                    tooltip.setWindowFlags(Qt.ToolTip)
                    tooltip.setStyleSheet(
                        """
                    QLabel {
                        background: "black";
                        color: "Dark Orange";
                        height: 30px;
                        width: 100px;
                        padding: 10px;
                        border: 2px solid black;
                    }
                    """
                    )
                    tooltip.move(QCursor.pos())
                    tooltip.show()
                    QTimer.singleShot(2000, tooltip.deleteLater)
                    # Store reference
                    self.data_mouse_tracker["currentTooltip"] = tooltip
            self.data_mouse_tracker["lastPosition"] = position
        finally:
            cmds.undoInfo(stateWithoutFlush=True)

    def get_object_under_cursor(self):
        """get the object under cursor."""
        pos = QCursor.pos()
        widget = QApplication.instance().widgetAt(pos)
        if widget is None:
            return
        relative_pos = widget.mapFromGlobal(pos)

        panel = cmds.getPanel(underPointer=True) or ""

        if not "modelPanel" in panel:
            return

        return (cmds.hitTest(panel, relative_pos.x(), relative_pos.y()) or [None])[0]

    def start_tracking_cursor(self):
        """Start tracking cursor."""
        self.data_mouse_tracker = {}

        timer = QTimer()
        # lower than 500 take too much CPU (single threaded)
        timer.setInterval(500)
        timer.timeout.connect(self.evaluate_mouse)
        timer.start()
        # Store reference
        self.data_mouse_tracker["timer"] = timer

    def stop_tracking_cursor(self):
        """Stop tracking cursor."""
        if self.data_mouse_tracker["timer"] is not None:
            self.data_mouse_tracker["timer"].stop()
            self.data_mouse_tracker["timer"] = None

    def job_event_selection_changed(self) -> None:
        """This function is meant to be called every time that the selection changes.
        Should run in the background so should not write into the undo history.
        - Impede selecting the perimeter and label indicator curve. 
        - Update the label indicators.
        - Change the text of the toggle label button to indicate better the effect.
        """
        # the curve that have a custom color cannot be made unselectable. The reference
        # or template mode would disable the assigned color.

        cmds.undoInfo(stateWithoutFlush=False)
        try:
            list_curve_perimeter = set()
            list_curve_indicator = set()
            list_curve_to_unselect = set()
            for curve in return_curve_in_scene()[0]:
                label_and_perimeter = False
                perimeter_and_pointer = False
                indicator_and_label = False
                if cmds.attributeQuery("connection_between_label_and_perimeter_curve",
                                       node=curve, exists=True):
                    label_and_perimeter = True
                if cmds.attributeQuery("connection_between_perimeter_and_pointer_curve",
                                       node=curve, exists=True):
                    perimeter_and_pointer = True
                if cmds.attributeQuery("connection_between_indicator_and_label_curve",
                                       node=curve, exists=True):
                    indicator_and_label = True

                if label_and_perimeter and perimeter_and_pointer:
                    list_curve_perimeter.add(curve)
                if label_and_perimeter and indicator_and_label:
                    list_curve_indicator.add(curve)
                if perimeter_and_pointer:
                    list_curve_to_unselect.add(curve)
                if indicator_and_label:
                    list_curve_to_unselect.add(curve)
                    layer_indicator = cmds.listConnections(
                        f"{curve}.connection_between_indicator_and_label_curve").pop()
                    list_curve_to_unselect.add(layer_indicator)

            current_sel = set(cmds.ls(selection=True, flatten=True))
            if len(current_sel & list_curve_to_unselect) != 0:
                cmds.select(current_sel - list_curve_to_unselect)
            if list_curve_indicator:
                update_label(list_curve_perimeter)
                self.but_toggle_label.setEnabled(True)
            else:
                self.but_toggle_label.setEnabled(False)

            if list_curve_indicator:
                list_mesh_selected = get_current_selected_mesh(
                    complain_if_more_selected=False, complain_if_none_selected=False)
                if list_mesh_selected:
                    self.but_toggle_label.setText("Toggle labels")
                else:
                    self.but_toggle_label.setText("Toggle all labels")
        finally:
            cmds.undoInfo(stateWithoutFlush=True)

    def closeEvent(self, event):  # pylint: disable=invalid-name
        """Kill all background processes when closing the window."""
        if event.spontaneous():
            print(f"{self.windowTitle()} window closed by user")
        else:
            print(f"{self.windowTitle()} window closed by Maya")
        self.stop_wait_for_zremesh()
        self.stop_tracking_cursor()
        self.stop_keep_focus()
        if self.job_unselect_curves:
            if isinstance(self.job_unselect_curves, int):
                if cmds.scriptJob(exists=self.job_unselect_curves):
                    cmds.scriptJob(kill=self.job_unselect_curves)
        if self.job_check_existences_posed_ref:
            if isinstance(self.job_check_existences_flat_ref, int):
                if cmds.scriptJob(exists=self.job_check_existences_flat_ref):
                    cmds.scriptJob(kill=self.job_check_existences_flat_ref)
        if self.job_check_existences_posed_ref:
            if isinstance(self.job_check_existences_flat_ref, int):
                if cmds.scriptJob(exists=self.job_check_existences_posed_ref):
                    cmds.scriptJob(kill=self.job_check_existences_posed_ref)


def start_main_ui():
    """Run the main UI. Checks if .zcr to launch has been compiled."""
    master_dir = Path(cmds.internalVar(userScriptDir=True)) / "ZR4M"
    tmp_dir = master_dir / "TMP"
    start_zbrush_bridge_zsc = master_dir / "start_zbrush_bridge.zsc"
    start_zbrush_bridge_txt = master_dir / "start_zbrush_bridge.txt"
    zremesh_settings = tmp_dir / "tmp_zremesh_settings.txt"

    if master_dir.is_dir() is False or tmp_dir.is_dir() is False:
        tmp_dir.mkdir(parents=True)
    if start_zbrush_bridge_zsc.is_file() is False:
        with open(start_zbrush_bridge_txt, "w", encoding='utf-8') as file_to_compile:
            text_to_compile = ("[If,1,\n"
                               f"[VarDef, start_zbrush_bridge, \"{zremesh_settings}\"]\n"
                               "[IPress,Zscript:Minimal Stroke]\n"
                               "[IPress,Zscript:Minimal Update]\n"
                               "[FileNameSetNext, start_zbrush_bridge]\n"
                               "[IPress,Zscript:Load]\n"
                               "]")
            file_to_compile.write(text_to_compile)

        with open(zremesh_settings, "w", encoding='utf-8') as dummy_settings_file:
            dummy_settings_file.write(
                '[if,1,[MessageOK, "The file has been successfully compiled", "Success"]]')

        cmds.confirmDialog(
            title="A required file is missing",
            message=(f"\nCompile this file first:\n\n{str(start_zbrush_bridge_txt)}\n\n"
                     "Launch Zbrush, open the Zscript tab and click on the load button.\n"
                     "Or use the shortcut CTRL+SHIFT+L\n\n"
                     "Note that requires Maya 2021 , Zbrush 2023 and Microsoft Windows.\n"
                     "GoZ is required only for transfer polypaint data.\n"),

            button=['Ok'],
            defaultButton='Ok',
            icon="warning"
        )
    else:
        if cmds.window("ZR4M", exists=True):
            cmds.deleteUI("ZR4M")
        list_path = (
            master_dir,
            tmp_dir,
            start_zbrush_bridge_zsc,
            start_zbrush_bridge_txt,
            zremesh_settings
        )
        Zr4mWindow(list_path).show()


start_main_ui()
