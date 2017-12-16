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
from PyQt5.QtCore import pyqtSignal, QObject, Qt
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtWidgets import *
from urllib.request import urlopen
import requests
from bs4 import BeautifulSoup
from configparser import ConfigParser
from datetime import datetime
from zipfile import ZipFile
from bson.objectid import ObjectId
import threading, queue
import json

LARRY_API = "https://frustrum.pictor.uberspace.de/larry/api"
CCAN_URL = "https://ccan.de/cgi-bin/ccan/ccan-view.pl?a=&ac=ty-ti-ni-tm-rp&sc=tm&so=d&nr=100000&pg=0&reveal=1"
lock = threading.Lock()

class DummyList(list):
    def addItem(self, item):
        return self.append(item)

class DownloadWorker(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.larry = kwargs['larry']
        self.daemon = True
    
    def run(self):
        while True:
            l = self.larry.download_queue.get() # [QListWidgetItem, str]
            
            try:
                l[0].download(l[1])
            except Exception as e:
                with lock:
                    self.larry.errors.append(e)
            
            finally:
                self.larry.download_queue.task_done()
            
class Entry(QObject):
    title = ""
    author = ""
    download_url = ""
    entry_url = ""
    date = None
    niveau = 0.0
    description = ""
    dependencies = []
    tags = []
    ids = {
        "file" : [],
        "author" : None,
        "upload" : None,
        "picture" : None
        }
    picture_url = ""
    
    def __init__(self):
        super().__init__()
        self.dependencies = list()
        self.tags = list()
        self.ids = {
            "file" : list(),
            "author" : None,
            "upload" : None,
            "picture" : None
            }
    
    @classmethod
    def fromUpload(cls, upload):
        self = cls()
        self.title = upload["title"]
        self.slug = upload["slug"]
        self.author = upload["author"]["username"]
        self.entry_url = ""
        self.download_url = ""
        self.date = datetime.strptime(upload["updatedAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
        self.niveau = float(upload["voting"]["sum"])
        self.description = upload["description"]
        self.dependencies = [] # no dependency handling yet
        self.ids = {
            "file" : [ObjectId(i) for i in (upload["file"] if isinstance(upload["file"], list) else [upload["file"]])],
            "author" : ObjectId(upload["author"]["_id"]),
            "upload" : ObjectId(upload["_id"])
            }
        
        return self
    
    _filename = None
    def filename(self):
        if self._filename:
            return self._filename
        
        r = requests.head(LARRY_API + "/media/" + str(self.ids["file"][0]), params={"download" : 1})
        self._filename = r.headers["Content-Disposition"].split('filename="')[-1].replace('"', '')
        return self._filename
    
    def __getitem__(self, item):
        return getattr(self, item)
    
    def __setitem__(self, item, val):
        return setattr(self, item, val)
    
    def version(self):
        return "OC" if self.filename()[-3:-1] == "oc" else "CR" # FIXME: Tag parsing
    
    def download(self, path : str):
        for id in self.ids["file"]:
            r = requests.get(LARRY_API + "/media/" + str(id), params={"download" : 1})
            filename = r.headers["Content-Disposition"].split('filename="')[-1].replace('"', '')
            with open(os.path.join(path, filename), "wb") as fobj:
                fobj.write(r.content)


class CCANEntry(Entry):
    def download(self, path : str):
        is_zip = self.download_url[-3:] == "zip"
        
        r = requests.get(self.download_url)
        realpath = os.path.join(path, self.filename())
        with open(realpath, "wb") as fobj:
            fobj.write(r.content)
        
        if is_zip:
            with ZipFile(realpath) as zip:
                zip.extractall()
            try:
                os.unlink(realpath)
            except OSError:
                pass
    
    def version(self):
        return self.download_url[-3:-1].upper()
    
    def filename(self):
        return self.download_url.split("/")[-1]
            
class LarryViewer(QMainWindow):
    bsobj = None
    content = []
    config = None
    error = False
    ui = True
    lsEntries = None
    threads = []
    errors = list()
    download_queue = None
    
    descLoaded = pyqtSignal(QListWidgetItem, QListWidgetItem, name="descLoaded")
    signal_error = pyqtSignal(str)
    signal_finished = pyqtSignal()
    
    def __init__(self, app : QApplication, bsobj : BeautifulSoup = None, ui : bool = True):
        super(type(self), self).__init__()
        if not os.path.exists("larryviewer.ini"):
            with open("larryviewer.ini", "w") as fobj:
                fobj.write("[CR]\nDirectory=\n\n[OC]\nDirectory=\n")
        
        self.config = ConfigParser()
        self.config.read("larryviewer.ini")
        self.bsobj = bsobj
        self.ui = ui
        self.dlgSettings = None
        self.threads = list()
        self.errors = list()
        self.download_queue = queue.Queue()
        for i in range(4):
            DownloadWorker(larry=self).start()
        
        if self.ui:
            uic.loadUi("main.ui", self)
            self.dlgSettings = uic.loadUi("settings.ui")
            
            # Main
            app.aboutToQuit.connect(self.aboutToQuit)
            self.lsEntries.currentItemChanged.connect(self.listItemChanged)
            self.lsEntries.itemDoubleClicked.connect(self.cycle)
            self.e_btnBack.clicked.connect(self.cycle)
            self.btnDownload.clicked.connect(self.download)
            self.e_btnDownload.clicked.connect(self.download)
            self.txtSearch.setEnabled(False)
            self.txtSearch.textChanged.connect(self.searchTextChanged)
            self.descLoaded.connect(self.listItemChanged)
            self.signal_error.connect(self.download_error)
            self.signal_finished.connect(self.download_finished)

            # Settings
            self.dlgSettings.hide()
            self.dlgSettings.lblCRDir.setText(self.config["CR"]["Directory"] or self.tr("Nicht festgelegt"))
            self.dlgSettings.lblOCDir.setText(self.config["OC"]["Directory"] or self.tr("Nicht festgelegt"))
            self.actSettings.triggered.connect(self.dlgSettings.show)
            self.dlgSettings.btnCRDir.clicked.connect(self.setClonkDir)
            self.dlgSettings.btnOCDir.clicked.connect(self.setClonkDir)
            self.dlgSettings.btnFinished.clicked.connect(self.dlgSettings.hide)
            
            # Larry uploader
            self.uploader = LarryUploader(self)
            self.uploader.hide()
            self.actUpload.triggered.connect(self.uploader.show)
            
        else:
            self.lsEntries = DummyList()
        
        if self.ui: # Don't load anything without UI
            threading.Thread(target=self.fetchCCANList, daemon=True).start() # WARNING: Don't call this before UI initialization, otherwise the application may fail
            threading.Thread(target=self.fetchLarryList, daemon=True).start()

    def fetchCCANList(self):
        if not self.bsobj:
            self.bsobj = BeautifulSoup(urlopen(CCAN_URL), "lxml")
        
        if not self.ui and not self.lsEntries:
            if self.ui:
                raise RuntimeError("QListWidget got deleted")
            else:
                self.lsEntries = DummyList()

        for row in self.bsobj.find_all("tr"):
            try:
                entry = row.find_all("td")
                upload = {}
                item = QListWidgetItem(entry[1].text)
                item.larry = CCANEntry()
                item.larry.author = entry[3].text
                item.larry.entry_url = "https://ccan.de/cgi-bin/ccan/{}".format(entry[1].a["href"])
                item.larry.download_url = "https://ccan.de/cgi-bin/ccan/{}".format(entry[2].a["href"])
                item.larry.date = datetime.strptime(entry[5].text, "%d.%m.%y %H:%M")
                item.larry.niveau =  0.0
                item.larry.description = ""
                item.larry.dependencies = []
                try:
                    item.larry.niveau = float(re.match(r".*\((.*)\)", entry[4].text).group(1))
                except ValueError:
                    pass
                
                self.lsEntries.addItem(item)

            except Exception as e:
                pass
                
            finally:
                if self.ui:
                    self.txtSearch.setEnabled(True)

    def loadDescription(self, entry):
        parser = BeautifulSoup(urlopen(entry.larry["entry_url"]), "lxml")

        for row in parser.find_all("tr"):
            cols = row.find_all("td")
            try:
                if cols[0].text in ["Beschreibung:", "Description:"]:
                    entry.larry.description = str(cols[1])
                    if self.ui:
                        self.descLoaded.emit(entry, entry)
                    break
            except IndexError:
                continue
    
    def fetchLarryList(self):
        if not self.ui and not self.lsEntries:
            if self.ui:
                raise RuntimeError("QListWidget got deleted")
            else:
                self.lsEntries = DummyList()
        
        root = requests.get(LARRY_API + "/uploads", headers={"Accept" : "application/json"}).json()
        
        for upload in root["uploads"]:
            if "file" not in upload:
                continue
            
            item = QListWidgetItem(upload["title"])
            item.larry = item.larry = Entry.fromUpload(upload)
            
            
            #font = item.font()
            #font.setItalic(True)
            #item.setFont(font)
            self.lsEntries.addItem(item)
    
    def reloadList(self) -> None:
        def _reload():
            self.fetchLarryList()
            self.fetchCCANList()
        self.lsEntries.clear()
        threading.Thread(target=_reload, daemon=True).start()

    def displayMessageBox(self, text : str, title : str = "LarryViewer", icon = QMessageBox.Information):
        if not self.ui:
            return
        
        box = QMessageBox()
        box.setWindowTitle(self.tr(title))
        box.setText(self.tr(text))
        box.setIcon(icon)
        box.exec()

    def displayErrorBox(self, text : str, title : str = "Fehler"):
        return self.displayMessageBox(text, title, QMessageBox.Critical)
    
    def validatePath(self, path):
        if os.path.exists(path) and not os.path.isfile(path):
            self.displayErrorBox("Kann \"{}\" nicht überschreiben.".format(path))
            return
        return path
    # Main
    def aboutToQuit(self):
        with open("larryviewer.ini", "w") as fobj:
            self.config.write(fobj)

    def listItemChanged(self, current : QListWidgetItem, previous : QListWidgetItem):
        if not current:
            return
        
        current.larry.description
        
        [i.setText(current.text()) for i in [self.lblTitle, self.e_lblTitle]]
        self.lblAuthor.setText(current.larry.author)
        
        if current.larry.description:
            [i.setHtml(current.larry.description) for i in [self.txtDescription, self.e_txtDescription]]
        else:
            [i.setHtml(self.tr("Lade...")) for i in [self.txtDescription, self.e_txtDescription]]
            threading.Thread(target=self.loadDescription, args=(current,)).start()

    def download(self, sel : QListWidgetItem = None):
        def _download(item : QListWidgetItem, path : str):
            def _add_dependencies(item : Entry, path : str):
                for d in item.dependencies:
                    for i in self.lsEntries:
                        print(i.larry.ids, d)
                        if i.larry.ids["upload"] == d and d not in self.download_queue.queue:
                            _add_dependencies(i.larry)
                self.download_queue.put((item, path))
            
            _add_dependencies(sel.larry, path)
            self.download_queue.join()
            if self.errors:
                e = "\n".join(str(i) for i in self.errors)
                self.errors = list()
                self.signal_error.emit(e)
            else:
                self.signal_finished.emit()
        
        sel = sel or self.lsEntries.currentItem()
        if not sel:
            return
        v = sel.larry.version()
        if not self.config[v]["Directory"]:
            return self.displayErrorBox("Kein Clonk-Verzeichnis angegeben!")
        
        self.sender().setEnabled(False)
        thread = threading.Thread(target=_download, args=(sel, self.config[v]["Directory"]))
        thread.start()

    def download_error(self, e : str):
        self.displayErrorBox(f"Fehler beim Download: {e}")
        [i.setEnabled(True) for i in [self.btnDownload, self.e_btnDownload]]

    def download_finished(self):
        self.displayMessageBox("Download erfolgreich!")
        [i.setEnabled(True) for i in [self.btnDownload, self.e_btnDownload]]

    def searchTextChanged(self, text : str):
        for i in range(self.lsEntries.count()):
            self.lsEntries.item(i).setHidden(text not in self.lsEntries.item(i).text())
    
    def cycle(self) -> None:
        i = self.mainStack.currentIndex()
        self.mainStack.setCurrentIndex(i + 1 if i + 1 < self.mainStack.count() else 0)
    
    # Settings

    def setClonkDir(self):
        f = QFileDialog.getExistingDirectory(self, self.tr("Verzeichnis auswählen"), os.getcwd())
        if f != "":
            v = self.sender().objectName()[3:5]
            self.config[v]["Directory"] = f
            with open("ccan_viewer.ini", "w") as fobj:
                self.config.write(fobj)

            getattr(self.dlgSettings, f"lbl{v}Dir").setText(f)

class LarryUploader(QDialog):
    credentials = {
        "username" : "",
        "email" : "",
        "password" : "",
        "token" : ""
        }
    current_upload = None
    
    def __init__(self, parent):
        super().__init__(parent)
        uic.loadUi("larry_uploader.ui", self)
        
        self.imgScene = QGraphicsScene()
        self.pctImage = QGraphicsView(self.imgScene)
        self.pctImage.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pctImage.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.layImage.addWidget(self.pctImage)
        
        self.btnAdvance.clicked.connect(self.advance)
        self.btnBack.clicked.connect(self.back)
        self.btnBack.setEnabled(False)
        
        self.tabs.setCurrentIndex(0)
        
        self.btnChooseImage.clicked.connect(self.chooseImage)
        self.btnChooseFiles.clicked.connect(self.chooseFiles)
    
    def rejectTabChange(self, index : int):
        if index == 0:
            email, password = self.txtEmail.text(), self.txtPassword.text()
            r = requests.post(LARRY_API + "/auth/login",
                          headers={"Content-Type" : "application/json"},
                          data=json.dumps({"user" : {"email" : email, "password" : password}})
                          )
            if r:
                
                user = r.json()["user"]
                self.credentials["username"] = user["username"]
                self.credentials["email"] = user["email"]
                self.credentials["token"] = user["token"]
                self.credentials["password"] = password
                return
            else:
                return self.tr("Benutzername und / oder Passwort inkorrekt.")
        
        elif index == 1:
            title, desc = self.txtTitle.text(), self.txtDescription.toPlainText()
            if not title:
                return "Titel fehlt."
            if not desc:
                return "Beschreibung fehlt."
            
            if [i for i in range(self.parent().lsEntries.count()) if self.parent().lsEntries.item(i).larry.title == title]:
                return "Es gibt bereits einen Eintrag mit diesem Titel."
            
            if not self.current_upload:
                self.current_upload = Entry()
            
            self.current_upload.title = title
            self.current_upload.author = self.credentials["username"]
            self.current_upload.description = desc
            return
            
        elif index == 2:
            if not getattr(self.current_upload, "files", None):
                return "Mindestens eine Datei muss hochgeladen werden."
            return
        
        elif index == 3:
            if self.chkAccepted.checkState() != Qt.Checked:
                return "Sie müssen den genannten Bedingungen zustimmen."
            if not self.upload():
                return
            
            self.parent().reloadList()
            self.hide()
            return
        
        else:
            return " "
    
    def advance(self):
        self.changeTab(+1)
    
    def back(self):
        self.changeTab(-1)
    
    def changeTab(self, offset):
        if offset > 0:
            self.btnAdvance.setEnabled(False)
            self.btnBack.setEnabled(False)
            txt = self.rejectTabChange(self.tabs.currentIndex())
            self.btnAdvance.setEnabled(True)
            self.btnBack.setEnabled(True)
            if txt:
                LarryViewer.displayMessageBox(self, txt, "Fehler", QMessageBox.Critical)
                return
        next = self.tabs.currentIndex() + offset
        
        self.btnAdvance.setText(self.tr("Weiter"))
        if next == 0:
            self.btnBack.setEnabled(False)
        elif next == self.tabs.count() - 1:
            self.btnAdvance.setText(self.tr("Fertigstellen"))
        
        self.tabs.setCurrentIndex(self.tabs.currentIndex() + offset)
    
    def chooseImage(self):
        # WARNING: Misusing Entry.picture_url to store a path
        self.current_upload.filename = QFileDialog.getOpenFileName(self, self.tr("Wähle die Bilddatei"), os.getcwd(), self.tr("Images (*.png *.jpg *.jpeg)"))[0]
        self.updateImage(self.current_upload.filename)
    
    def updateImage(self, f : str):
        self.imgScene.clear()
        img = QImage(f).scaled(self.pctImage.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QGraphicsPixmapItem(QPixmap.fromImage(img))
        self.imgScene.addItem(pixmap)
        self.pctImage.update()
    
    def chooseFiles(self):
        # WARNING: Misusing Entry.ids["file"] to store file paths
        files = QFileDialog.getOpenFileNames(self, self.tr("Wähle die Dateien"), os.getcwd(), self.tr("Clonk Files (*.c4d *.c4f *.c4b *.c4r *.c4s *.c4m *.c4g *.ocd *.ocf *.ocb *.ocr *.ocs *ocm"))[0]
        for f in files:
            self.lsFiles.addItem(QListWidgetItem(f))
        
        self.current_upload.files = files 
    
    def upload(self):
        def uploadFile(f):
            with open(f, "rb") as fobj:
                r = requests.post(LARRY_API + "/media",
                                headers = {
                                    #"Content-Type" : "multipart/form-data",
                                    "Authorization" : f"Bearer {self.credentials['token']}",
                                    "Accept" : "application/json"
                                        },
                                files = {"media" : fobj}
                                )
                if r:
                    return r.json()["_id"]
        
        self.current_upload.date = datetime.now()
        self.current_upload.slug = self.current_upload.title.replace(" ", "_").encode("ascii", "ignore").decode("ascii")
        
        [self.current_upload.ids["file"].append(uploadFile(i)) for i in self.current_upload.files]
        
        if self.current_upload.picture_url:
            self.current_upload.ids["picture"] = uploadFile(self.current_upload.picture_url)
        
        data = {
            "upload" : {
                "title" : self.current_upload.title,
                "slug" : self.current_upload.slug,
                "description" : self.current_upload.description,
                "tag" : ["LarryViewer"],
                "files" : [i for i in self.current_upload.ids["file"] if i is not None]
                }
            }
        
        if self.current_upload.ids["picture"]:
            data["pic"] = self.current_upload.ids["picture"]
        
        r = requests.post(LARRY_API + "/uploads",
                          headers = {"Content-Type" : "application/json", "Authorization": f"Bearer {self.credentials['token']}"},
                          data = json.dumps(data))
        
        if r:
            LarryViewer.displayMessageBox(self, "Upload erfolgreich!", "Upload")
            return True
        else:
            LarryViewer.displayMessageBox(self, "Upload fehlgeschlagen!", "Fehler", QMessageBox.Critical)
            return False
    
if __name__ == "__main__":
    app = QApplication(sys.argv)
    v = LarryViewer(app)
    v.show()
    sys.exit(app.exec())
