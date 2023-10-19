"""
Containes the UI part of ZR4M script
"""

import os
import subprocess
import time
from io import TextIOWrapper
from pathlib import Path
from random import random
from typing import Union

import maya.api.OpenMaya as om2
import maya.cmds as cmds
from maya import OpenMayaUI
from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QCursor
from PySide2.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QMainWindow, QPushButton, QSizePolicy,
                               QSlider, QSpacerItem, QSpinBox, QVBoxLayout,
                               QWidget)
from shiboken2 import wrapInstance

from ZR4M.ZR4M import *
from ZR4M import *

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
         self.start_zbrush_bridge_txt, self.zremesh_settings,
           self.disable_zremesh_bridge) = list_path

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

        if self.disable_zremesh_bridge:
            self.groupbox_zremesh.hide()
            self.groupbox_garment.setChecked(True)
            self.groupbox_garment.setCheckable(False)

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

        button_set_ref_layout.addWidget(self.but_set_flat_ref)
        button_set_ref_layout.addWidget(self.but_set_posed_ref)

        button_transfer_attribute.addWidget(self.but_uv_from_flat_ref)
        button_transfer_attribute.addWidget(
            self.but_position_from_posed_ref)

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
                    dummy_mat,dummy_shader_group = create_material("dummy_mat")#goz chash if texture
                    cmds.sets(duplicate_geometry, forceElement=dummy_shader_group)
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
                    cmds.delete(dummy_mat,dummy_shader_group)

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

# pylint: disable=invalid-name
def start_ZR4M_ui(disable_zremesh_bridge: bool=False):
    """Run the main UI.
    Args:
        disable_zremesh_bridge (bool, optional): Checks if .zcr to launch has been compiled.
          Defaults to True.
    """
    master_dir = Path(cmds.internalVar(userScriptDir=True)) / "ZR4M"
    tmp_dir = master_dir / "TMP"
    start_zbrush_bridge_zsc = master_dir / "start_zbrush_bridge.zsc"
    start_zbrush_bridge_txt = master_dir / "start_zbrush_bridge.txt"
    zremesh_settings = tmp_dir / "tmp_zremesh_settings.txt"

    if master_dir.is_dir() is False or tmp_dir.is_dir() is False:
        tmp_dir.mkdir(parents=True)
    if start_zbrush_bridge_zsc.is_file() is False and disable_zremesh_bridge is False:
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
            zremesh_settings,
            disable_zremesh_bridge
        )
        Zr4mWindow(list_path).show()


start_ZR4M_ui()
