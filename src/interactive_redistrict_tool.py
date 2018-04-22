# -*- coding: utf-8 -*-
"""LINZ Redistricting Plugin - Interactive Redistricting Tool

.. note:: This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.
"""

__author__ = '(C) 2018 by Nyall Dawson'
__date__ = '20/04/2018'
__copyright__ = 'Copyright 2018, The QGIS Project'
# This will get replaced with a git SHA1 when you do a git archive
__revision__ = '$Format:%H$'

from qgis.PyQt.QtCore import (Qt,
                              QSizeF,
                              QPointF)
from qgis.PyQt.QtGui import (QImage,
                             QPainter,
                             QFontMetrics)
from qgis.core import (QgsFeatureRequest,
                       QgsGeometry,
                       QgsPointLocator,
                       QgsTextFormat,
                       QgsRenderContext,
                       QgsTextRenderer)
from qgis.gui import (QgsMapTool,
                      QgsSnapIndicator,
                      QgsMapCanvasItem)
from qgis.utils import iface


class UniqueFeatureEdgeMatchCollectingFilter(QgsPointLocator.MatchFilter):

    def __init__(self):
        super().__init__()
        self.matches = []

    def acceptMatch(self, match):
        if match.type() not in (QgsPointLocator.Area, QgsPointLocator.Edge):
            return False

        if match.distance() > 1000:
            return False

        existing_matches = [m for m in self.matches if
                            m.layer() == match.layer() and m.featureId() == match.featureId()]
        if not existing_matches:
            self.matches.append(match)
            return True
        else:
            return False

    def get_matches(self):
        return self.matches


class InteractiveRedistrictingTool(QgsMapTool):
    def __init__(self, canvas, meshblock_layer, district_layer):
        super().__init__(canvas)
        self.meshblock_layer = meshblock_layer
        self.district_layer = district_layer

        self.snap_indicator = QgsSnapIndicator(self.canvas())
        self.pop_decorator = None

        self.is_active = False
        self.districts = None
        self.current_district = None
        self.modified = set()

    def get_matches(self, event):
        point = event.mapPoint()
        match_filter = UniqueFeatureEdgeMatchCollectingFilter()
        match = self.canvas().snappingUtils().snapToMap(point, match_filter)
        return match_filter.matches

    def get_districts(self, matches):
        features = self.get_meshblocks(matches)
        return set([f['GeneralConstituencyCode'] for f in features])

    def get_meshblocks(self, matches):
        feature_ids = [match.featureId() for match in matches]
        features = [f for f in self.meshblock_layer.getFeatures(QgsFeatureRequest().setFilterFids(feature_ids))]
        return features

    def check_valid_matches(self, districts):
        return len(districts) == 2

    def canvasMoveEvent(self, event):
        if not self.is_active:
            # snapping tool - show indicator
            matches = self.get_matches(event)
            if self.check_valid_matches(self.get_districts(matches)):
                # we require exactly 2 matches from different districts -- cursor must be over a border
                # of two features
                self.snap_indicator.setMatch(matches[0])
            else:
                self.snap_indicator.setMatch(QgsPointLocator.Match())
        elif self.districts:
            matches = self.get_matches(event)
            p = QgsGeometry.fromPointXY(event.mapPoint())
            meshblocks = [m for m in self.get_meshblocks(matches) if m.id() not in self.modified and m.geometry().intersects(p)]
            if len(meshblocks) == 1:
                meshblock = meshblocks[0]
                old_district = meshblock['GeneralConstituencyCode']
                if not self.current_district:
                    candidates = [d for d in self.districts if d != old_district]
                    if candidates:
                        self.current_district = candidates[0]
                if self.current_district and old_district and self.current_district != old_district:
                    iface.messageBar().pushMessage('{}: {} -> {}'.format(meshblock['Meshblock'],old_district, self.current_district))
                    self.modified.add(meshblock.id())
                    self.meshblock_layer.changeAttributeValue(meshblock.id(),18,self.current_district)
                    self.meshblock_layer.triggerRepaint()

    def canvasPressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            return

        if self.is_active or event.button() == Qt.RightButton:
            if self.pop_decorator is not None:
                self.canvas().scene().removeItem(self.pop_decorator)
                self.pop_decorator = None
                self.canvas().update()
            self.is_active = False
            self.districts = None
            self.current_district = None
        else:
            matches = self.get_matches(event)
            districts = self.get_districts(matches)
            self.current_district = None
            self.modified = set()
            if self.check_valid_matches(districts):
                self.is_active = True
                self.districts = districts
                self.pop_decorator = CentroidDecorator(self.canvas(), self.district_layer)
                self.canvas().update()


class CentroidDecorator(QgsMapCanvasItem):

    def __init__(self, canvas, layer, mode=0):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.text_format = QgsTextFormat()
        #self.text_format.shadow().setEnabled(True)
        self.text_format.background().setEnabled(True)
        self.text_format.background().setSize(QSizeF(1, 0))
        self.text_format.background().setOffset(QPointF(0, -0.7))
        self.text_format.background().setRadii(QSizeF(1, 1))
        self.mode = mode

    def paint(self, painter, option, widget):
        image_size = self.canvas.mapSettings().outputSize()
        image = QImage(image_size.width(), image_size.height(), QImage.Format_ARGB32)
        image.fill(0)
        image_painter = QPainter(image)
        render_context = QgsRenderContext.fromQPainter(image_painter)
        if True:
            image_painter.setRenderHint(QPainter.Antialiasing, True)

            rect = self.canvas.mapSettings().visibleExtent()
            line_height = QFontMetrics(painter.font()).height()
            for f in self.layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                if self.mode == 0:
                    pole, dist = f.geometry().clipped(rect).poleOfInaccessibility(3000)
                else:
                    pole = f.geometry().centroid()
                pixel = self.toCanvasCoordinates(pole.asPoint())

                text_string = ['{}'.format(f['GeneralConstituencyCode']),'{}'.format(int(f['Shape_Length']))]  # ,'M: {}'.format(int(f['Shape_Length']*.5))]
                # print(pixel.toPoint())
                QgsTextRenderer().drawText(QPointF(pixel.x(), pixel.y()), 0, QgsTextRenderer.AlignCenter,
                                           text_string, render_context, self.text_format)
        # finally:
        image_painter.end()

        painter.drawImage(0, 0, image)

