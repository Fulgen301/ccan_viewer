#!/usr/bin/env python3
#-*- coding: utf-8 -*-

# Copyright (c) 2017, George Tokmaji

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os, re
sys = os.sys

from PyQt5 import uic
from PyQt5.QtCore import pyqtSignal, QThread
from PyQt5.QtWidgets import *
from urllib.request import urlopen
from bs4 import BeautifulSoup
from configparser import ConfigParser
from _thread import start_new_thread

class DownloadThread(QThread):
	error_signal = pyqtSignal()
	finished_signal = pyqtSignal()

	def __init__(self, parent, path, url):
		super(type(self), self).__init__(parent)
		self.path = path
		self.url = url

	def run(self):
		try:
			with open(self.path, "wb") as fobj:
				urlobj = urlopen(self.url)
				while True:
					chunk = urlobj.read(1024)
					if not chunk:
						break
					fobj.write(chunk)
			self.finished_signal.emit()

		except Exception as e:
			try:
				os.unlink(path)
			except OSError:
				pass

			self.error_signal.emit()


class CCANViewer(QMainWindow):
	url = "https://ccan.de/cgi-bin/ccan/ccan-view.pl?a=&ac=ty-ti-ni-tm-rp&sc=tm&so=d&nr=100000&pg=0&reveal=1"
	bsobj = None
	content = []
	config = None
	no_load = False
	error = False

	def __init__(self, app : QApplication, bsobj : BeautifulSoup = None, no_load = False):
		super(type(self), self).__init__()
		self.config = ConfigParser()
		self.config.read("ccan_viewer.ini")
		self.bsobj = bsobj
		self.no_load = no_load

		uic.loadUi("ccan_viewer.ui", self)
		
		# Main
		app.aboutToQuit.connect(self.aboutToQuit)
		self.lsEntries.currentItemChanged.connect(self.listItemChanged)
		self.btnDownload.clicked.connect(self.download)
		self.txtSearch.setEnabled(False)
		self.txtSearch.textChanged.connect(self.searchTextChanged)

		# Settings
		self.dlgSettings.hide()
		self.lblClonkDir.setText(self.config["Clonk"]["Directory"] or self.tr("Nicht festgelegt"))
		self.actSettings.triggered.connect(self.showSettings)
		self.btnClonkDir.clicked.connect(self.setClonkDir)
		self.btnSetFinished.clicked.connect(self.hideSettings)

		start_new_thread(self.fetchCCANList, ()) # WARNING: Don't call this before UI initialization, otherwise the application may fail


	def fetchCCANList(self):
		if not self.bsobj:
			self.bsobj = BeautifulSoup(urlopen(self.url), "html.parser")

		for row in self.bsobj.find_all("tr"):
			try:
				entry = row.find_all("td")
				item = QListWidgetItem(entry[1].text)
				item.ccan = {
					"author" : entry[3].text,
					"entry_url" : "https://ccan.de/cgi-bin/ccan/{}".format(entry[1].a["href"]),
					"download_url" : "https://ccan.de/cgi-bin/ccan/{}".format(entry[2].a["href"]),
					"niveau" : 0.0,
					"description" : ""
					}
				try:
					item.ccan["niveau"] = float(re.match(r".*\((.*)\)", entry[4].text).group(1))
				except ValueError:
					pass

				item.ccan["description"] = self.tr("Lade...")
				self.lsEntries.addItem(item)

			except Exception as e:
				print(e, file=sys.stderr)
			finally:
				self.txtSearch.setEnabled(True)

		if self.no_load:
			return

		for i in range(self.lsEntries.count()):
			try:
				self.lsEntries.item(i).ccan["description"] = self._loadDescription(self.lsEntries.item(i).ccan["entry_url"])
			except Exception as e:
				print(e, file=sys.stderr)

	def _loadDescription(self, url):
		parser = BeautifulSoup(urlopen(url), "html.parser")

		for row in parser.find_all("tr"):
			cols = row.find_all("td")
			try:
				if cols[0].text == "Beschreibung:":
					for br in cols[1].find_all("br"):
						br.replace_with("\n")
					return cols[1].text
			except IndexError:
				continue

	def displayMessageBox(self, text : str, title : str = "CCAN Viewer", icon = QMessageBox.Information):
		box = QMessageBox()
		box.setWindowTitle(self.tr(title))
		box.setText(self.tr(text))
		box.setIcon(icon)
		box.exec()

	def displayErrorBox(self, text : str, title : str = "Fehler"):
		return self.displayMessageBox(text, title, QMessageBox.Critical)

	# Main
	def aboutToQuit(self):
		with open("ccan_viewer.ini", "w") as fobj:
			self.config.write(fobj)

	def listItemChanged(self, current : QListWidgetItem, previous : QListWidgetItem):
		if not current:
			return

		self.lblTitle.setText(current.text())
		self.lblAuthor.setText(current.ccan["author"])
		self.txtDescription.setText(current.ccan["description"])

	def download(self):
		if not self.config["Clonk"]["Directory"]:
			return self.displayErrorBox("Kein Clonk-Verzeichnis angegeben!")

		sel = self.lsEntries.currentItem()
		if not sel:
			return

		path = os.path.join(self.config["Clonk"]["Directory"], sel.ccan["download_url"].split("/")[-1])
		if os.path.exists(path) and not os.path.isfile(path):
			return self.displayErrorBox("Kann \"{}\" nicht überschreiben.".format(path))

		self.btnDownload.setEnabled(False)

		thread = DownloadThread(self, path, sel.ccan["download_url"])
		thread.error_signal.connect(self.download_error)
		thread.finished_signal.connect(self.download_finished)
		thread.start()

	def download_error(self):
		self.displayErrorBox("Fehler beim Download.")
		self.btnDownload.setEnabled(True)

	def download_finished(self):
		self.displayMessageBox("Download erfolgreich!")
		self.btnDownload.setEnabled(True)

	def searchTextChanged(self, text : str):
		for i in range(self.lsEntries.count()):
			self.lsEntries.item(i).setHidden(self.lsEntries.item(i).text().find(text) == -1)
		

	# Settings
	def showSettings(self):
		self.dlgSettings.show()

	def hideSettings(self):
		self.dlgSettings.hide()

	def setClonkDir(self):
		f = QFileDialog.getExistingDirectory(self, self.tr("Verzeichnis auswählen"), os.getcwd())
		if f != "":
			self.config["Clonk"]["Directory"] = f
			with open("ccan_viewer.ini", "w") as fobj:
				self.config.write(fobj)

			self.lblClonkDir.setText(f)

if __name__ == "__main__":
	app = QApplication(sys.argv)
	#v = CCANViewer(app, BeautifulSoup(open("ccan.txt").read(), "html.parser"), False)
	v = CCANViewer(app)
	v.show()
	app.exec()