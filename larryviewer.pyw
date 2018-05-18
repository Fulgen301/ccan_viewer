#!/usr/bin/env python3
#-*- coding: utf-8 -*-

#Copyright (c) 2018, George Tokmaji

#Permission to use, copy, modify, and/or distribute this software for any
#purpose with or without fee is hereby granted, provided that the above
#copyright notice and this permission notice appear in all copies.

#THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
#WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
#MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
#ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
#WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
#ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
#OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import os, re
sys = os.sys

from PySide2.QtUiTools import QUiLoader
from PySide2.QtCore import Signal, QObject, QSettings, Qt, qFatal, qInstallMessageHandler
from PySide2.QtGui import QPixmap, QImage, QPalette, QBrush
from PySide2.QtWidgets import *
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import zipfile
from bson.objectid import ObjectId
import traceback
import threading, queue
import json
import base64
import c4group
import chardet
import configparser
import io
import mistune

LARRY_API = "https://larry-api.asw.io"
#LARRY_API = "http://127.0.0.1:8080"
CCAN_URL = "https://ccan.de/cgi-bin/ccan/ccan-view.pl?a=&sc=tm&so=d&nr=100000&pg=0&ac=ty-ti-ni-tm-rp-ev&reveal=1"
CCAN_DOWNLOAD_URL = "https://ccan-data.ps-cdn.net/data/"
lock = threading.Lock()

viewer = None

def loadUi(ui : str, obj=None):
    loader = QUiLoader()
    return loader.load(ui, obj)

def decodeGroupFile(b):
    return b.decode(chardet.detect(b)["encoding"])

class DummyZipFile(zipfile.ZipFile):
    def __init__(self, fp):
        self.debug = 0
        self.fp = fp
        self.filelist = []
        self.NameToInfo = {}
        self._writing = False
    
    def getContents(self):
        self._RealGetContents()
        return list(self.NameToInfo.keys())
    
    def __del__(self):
        return

class LarryWorker(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)
        self.larry = kwargs['larry']
        self.daemon = True

class DownloadWorker(LarryWorker):
    def run(self):
        while True:
            l = self.larry.queues["download"].get() # [Entry, str]
            print(l)
            try:
                self.larry.dlgProgress.setLabelText(f"Lade {l[0].title} herunter...")
                l[0].download(l[1])
            except Exception as e:
                print(e)
                with lock:
                    self.larry.errors.append(e)
            
            finally:
                self.larry.queues["download"].task_done()

class PropertyWorker(LarryWorker):
    def run(self):
        while True:
            entry = self.larry.queues["desc"].get()
            
            parser = BeautifulSoup(requests.get(entry.larry["entry_url"]), "lxml")
            for row in parser.find_all("tr"):
                cols = row.find_all("td")
                try:
                    if cols[0].text in ["Beschreibung:", "Description:"]:
                        entry.larry.description = str(cols[1])
                        self.larry.descLoaded.emit(entry, entry)
                        break
                except IndexError:
                    continue
            self.larry.queues["desc"].task_done()

class ImageWorker(LarryWorker):
    def run(self):
        while True:
            entry = self.larry.queues["image"].get()
            #if not entry.larry.picture and entry.larry.ids["picture"]:
            #    r = requests.get(f"{LARRY_API}/media/{str(entry.larry.ids['picture'])}")
            self.larry.queues["image"].task_done()
            
            

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
    _files = None
    
    picture = None
    _version = ""
    
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
            "file" : [ObjectId(i) for i in (upload["files"] if isinstance(upload["files"], list) else [upload["files"]])],
            "upload" : ObjectId(upload["id"]),
            "picture" : ObjectId(upload["pic"])
            }
        self._version = str(upload["__v"])
        
        return self
    
    def files(self):
        if not self._files:
            self._files = []
            for i in self.ids["file"]:
                r = requests.head(LARRY_API + "/media/" + str(i), params={"download" : 1})
                if r:
                    self._files.append(r.headers["Content-Disposition"].split('filename="')[-1].replace('"', ''))
        
        return self._files
    
    def __getitem__(self, item):
        return getattr(self, item)
    
    def __setitem__(self, item, val):
        return setattr(self, item, val)
    
    def __getstate__(self):
        s = self.__dict__.copy()
       
        for i in s["ids"]:
            if i == "file":
                 s["ids"][i] = [str(i) for i in s["ids"][i]]
            else:
                s["ids"][i] = str(s["ids"][i])
        return s
    
    def __setstate__(self, s):
        QObject.__init__(self)
        for i in s["ids"]:
            if i == "file":
                try:
                    s["ids"][i] = [ObjectId(i) for i in s["ids"][i]]
                except Exception:
                    pass #FIXME
            else:
                try:
                    s["ids"][i] = ObjectId(s["ids"][i])
                except Exception:
                    pass #FIXME
        self.__dict__.update(s)
    
    def filePath(self, f):
        return LARRY_API + "/media/" + f
    
    def version(self):
        return self._version
    def clonkVersion(self):
        return "OC" if self.filename()[-3:-1] == "oc" else "CR" # FIXME: Tag parsing
    
    def picture(self):
        return LARRY_API + "/media/" + str(self.ids["picture"])
    
    def download(self, path : str):
        for f in self.files(): # FIXME: We assume that self.files() stays in the same order as the id list.
            if not os.path.exists(os.path.join(path, f)):
                r = requests.get(LARRY_API + "/media/" + str(f), params={"download" : 1})
                with open(os.path.join(path, f), "wb") as fobj:
                    fobj.write(r.content)
    
    def comments(self):
        return []

class CCANEntry(Entry):
    _start = 0
    _clonkVersion = "CR"
    _end = -1
    _buffer = None
    
    def __init__(self, *args, **kwargs):
        super(CCANEntry, self).__init__(*args, **kwargs)
        self._buffer = io.BytesIO()
    
    def __getstate__(self):
        s = super().__getstate__()
        for i in ["_clonkVersion", "_end"]:
            s[i] = self[i]
        
        #FIXME: pickle files
        return s
    
    def _isZip(self):
        return self.download_url.endswith("zip")
    
    def download(self, path : str):
        def _download(f):
            print("Downloading")
            r = requests.get(self.download_url, headers=({"Range" : f"bytes=0-{self._start - 1}"} if self._isZip() else {}))
            realpath = os.path.join(path, f)
            print("Realpath", realpath)
            with open(realpath, "wb") as fobj:
                fobj.write(r.content)
                self._buffer.seek(0)
                fobj.write(self._buffer.read())
                self._buffer.seek(0)
            return realpath
        
        if not self._isZip():
            for i in self.files():
                if not os.path.exists(os.path.join(path, i)):
                    _download(i)
        
        else:
            realpath = _download(self.download_url.split("/")[-1])
            print("Extracting")
            with zipfile.ZipFile(realpath) as zip:
                self._files = zip.namelist() # FIXME: Determine if that line is really needed
                zip.extractall(os.path.split(realpath)[0])
            try:
                os.unlink(realpath)
            except OSError:
                pass
    
    def clonkVersion(self):
        return "CR" if self._clonkVersion != "OC" else "OC"
    
    def picture(self):
        return ""
    
    def filePath(self, f):
        return self.download_url
    
    def files(self):
        if not self._files:
            filename = self.download_url.split("/")[-1]
            if self._isZip():
                r = requests.head(self.download_url)
                if not r:
                    raise Exception(r.reason)
                
                #print(r.url)
                try:
                    r.headers["Location"]
                except:
                    return []
                if "legacy" in r.headers["Location"]: # CCAN /legacy/ URLs return garbage Content-Length. Let's ignore those archives.
                    self._files = []
                    return self._files
                if not r:
                    raise Exception(r.reason)
                else:
                    r = requests.head(r.headers["Location"])
                    #print(r.headers)
                    self._buffer = io.BytesIO()
                    end = self._start = int(r.headers["Content-Length"])
                    self._files = None
                    while True:
                        self._start = max(self._start - 50000, 0)
                        print(self._start, end)
                        headers = {"Range" : f"bytes={self._start}-{end}"}
                        print("Headers", headers)
                        i = requests.get(CCAN_DOWNLOAD_URL + filename, headers=headers)
                        self._buffer.seek(0)
                        old = self._buffer.read()
                        self._buffer.seek(0)
                        self._buffer.write(i.content)
                        self._buffer.write(old)
                        print("Buffer length:", self._buffer.getbuffer().nbytes)
                        z = DummyZipFile(self._buffer)
                        try:
                            self._files = z.getContents()
                            assert self._files
                            break
                        except zipfile.BadZipFile as e:
                            if self._start == 0:
                                raise Exception("Couldn't get table of contents. Maybe the zip file is damaged.") from e
                            
                            end = self._start - 1
                        except ValueError:
                            self._files = []
                            return self._files
            else:
                self._files = [filename]
        return self._files

class LarryViewer(QMainWindow):
    bsobj = None
    content = []
    config = None
    error = False
    ui = None
    lsEntries = None
    threads = []
    errors = list()
    queues = None
    dlgProgress = None
    dlgSettings = None
    parser = None
    
    descLoaded = Signal(QListWidgetItem, QListWidgetItem, name="descLoaded")
    signal_error = Signal(str)
    signal_finished = Signal(Entry, str)
    
    def __init__(self, bsobj : BeautifulSoup = None):
        super().__init__()
        self.parser = mistune.Markdown()
        self.config = QSettings()
        self.bsobj = bsobj
        self.dlgProgress = QProgressDialog()
        self.dlgProgress.setCancelButton(None)
        self.dlgProgress.reset()
        self.threads = list()
        self.errors = list()
        self.queues = {
            "download" : queue.Queue(),
            "desc" : queue.Queue(),
            "image" : queue.Queue()
            }
        
        DownloadWorker(larry=self).start()
        for i in range(2):
            PropertyWorker(larry=self).start()
            #ImageWorker(larry=self).start()
        
        self.ui = loadUi("main.ui", self)
        self.dlgSettings = loadUi("settings.ui")
        
        # Main
        self.ui.actAboutQt.triggered.connect(QApplication.aboutQt)
        self.ui.lsEntries.currentItemChanged.connect(self.listItemChanged)
        self.ui.lsEntries.itemDoubleClicked.connect(self.cycle)
        self.ui.btnDownload.clicked.connect(self.download)
        self.ui.txtSearch.textChanged.connect(self.searchTextChanged)
        self.descLoaded.connect(self.listItemChanged)
        self.signal_error.connect(self.downloadError)
        self.signal_finished.connect(self.downloadFinished)

        # Settings
        self.dlgSettings.hide()
        self.dlgSettings.lblCRDir.setText(self.config.value("CR/directory") or self.tr("Nicht festgelegt"))
        self.dlgSettings.lblOCDir.setText(self.config.value("OC/directory") or self.tr("Nicht festgelegt"))
        self.ui.actSettings.triggered.connect(self.dlgSettings.show)
        self.dlgSettings.btnCRDir.clicked.connect(self.setClonkDir)
        self.dlgSettings.btnOCDir.clicked.connect(self.setClonkDir)
        self.dlgSettings.btnFinished.clicked.connect(self.dlgSettings.hide)
        
        # Larry uploader
        self.uploader = LarryUploader(self)
        self.uploader.ui.hide()
        self.ui.actUpload.triggered.connect(self.uploader.ui.show)
        
        threading.Thread(target=self.fetchCCANList, name="ccanlist", daemon=True).start() # WARNING: Don't call this before UI initialization, otherwise the application may fail
        threading.Thread(target=self.fetchLarryList, name="larrylist", daemon=True).start()
    
    def itemEqual(self, b):
        assert isinstance(self, QListWidgetItem)
        if not b:
            return
        return b.larry == self.larry
    
    def fetchCCANList(self):
        if not self.bsobj:
            self.bsobj = BeautifulSoup(requests.get(CCAN_URL), "lxml")
        for row in self.bsobj.find_all("tr"):
            try:
                entry = row.find_all("td")
                upload = {}
                item = QListWidgetItem(entry[1].text)
                item.__eq__ = self.itemEqual
                item.larry = CCANEntry()
                item.larry.title = entry[1].text
                item.larry._version = "v" + entry[1].text.partition(" v")[-1]
                item.larry.author = entry[3].text
                item.larry.entry_url = "https://ccan.de/cgi-bin/ccan/{}".format(entry[1].a["href"])
                item.larry.download_url = "https://ccan.de/cgi-bin/ccan/{}".format(entry[2].a["href"])
                item.larry.date = datetime.strptime(entry[6].text, "%d.%m.%y %H:%M")
                item.larry.niveau =  0.0
                item.larry.description = ""
                item.larry.dependencies = None
                item.larry._clonkVersion = entry[4].text
                try:
                    item.larry.niveau = float(re.match(r".*\((.*)\)", entry[5].text).group(1))
                except ValueError:
                    pass
                
                self.ui.lsEntries.addItem(item)
            
            except Exception:
                pass
    
    def fetchLarryList(self):
        root = requests.get(LARRY_API + "/uploads", headers={"Accept" : "application/json"}).json()
        
        for upload in root["uploads"]:
            if "files" not in upload:
                continue
            
            item = QListWidgetItem(upload["title"])
            item.__eq__ = self.itemEqual
            item.larry = Entry.fromUpload(upload)
            
            #font = item.font()
            #font.setItalic(True)
            #item.setFont(font)
            self.ui.lsEntries.addItem(item)
    
    def reloadList(self) -> None:
        def _reload():
            self.fetchLarryList()
            self.fetchCCANList()
        self.ui.lsEntries.clear()
        threading.Thread(target=_reload, daemon=True).start()

    def displayMessageBox(self, text : str, title : str = "LarryViewer", icon = QMessageBox.Information):
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

    def listItemChanged(self, current : QListWidgetItem, previous : QListWidgetItem):
        if not current:
            return
        
        self.ui.lblTitle.setText(current.text())
        self.ui.lblAuthor.setText(current.larry.author)
        
        if current.larry.description:
            self.ui.txtDescription.setHtml(self.parser(current.larry.description))
        else:
            self.ui.txtDescription.setHtml(self.tr("Lade..."))
            self.queues["desc"].put(current)
        
        #if current.larry.ids.get("picture"):
            #if current.larry.picture:
                #self.txtDescription.setStyleSheet(s)
            #else:
                #self.queues["image"].put(current)
    
    def resolveDependenciesAfterInstallation(self, item : Entry, path : str):
        def _checkInEntries(iterable):
            #print(list(iterable))
            for l in iterable:
                if not os.path.exists(os.path.join(path, l)):
                    print(f"{l} doesn't exist")
                    for i in range(self.ui.lsEntries.count()):
                        if l in self.ui.lsEntries.item(i).larry.files():
                            print(f"Found")
                            self.setLabelText(f"Füge Abhängigkeit hinzu: {self.ui.lsEntries.item(i).text()}")
                            self.queues["download"].put((self.ui.lsEntries.item(i).larry, path))
                            break
        
        def _add_dependencies(item : Entry):
            # If we're called, there are no dependencies specified or it is a CCAN entry
            # First: check for dependencies.txt in one of the files
            self.dlgProgress.setLabelText("Löse Abhängigkeiten auf...")
            for i in item.files():
                realpath = os.path.join(path, i)
                if not os.path.isfile(realpath):
                    continue
                grp = c4group.C4Group()
                grp.Open(realpath)
                if grp.AccessEntry("Dependencies.txt"):
                    print("dependencies.txt found")
                    b = bytearray(grp.EntrySize("Dependencies.txt"))
                    grp.Read(b)
                    _checkInEntries(b.decode(chardet.detect(b)["encoding"]).splitlines())
                elif grp.AccessEntry("Scenario.txt"):
                    print("Scenario.txt found")
                    c = configparser.ConfigParser()
                    b = bytearray(grp.EntrySize("Scenario.txt"))
                    grp.Read(b)
                    c.read_string(decodeGroupFile(b).replace("\0",""))
                    _checkInEntries((i[1] for i in c["Definitions"].items() if print(i) or re.match(r"definition*", i[0])))
            self.queues["download"].join()
            self.signal_finished.disconnect(self.resolveDependenciesAfterInstallation)
            self.signal_finished.connect(self.downloadFinished)
            self.signal_finished.emit(item, path)

        assert item.files(), f"{item.files()}"
        threading.Thread(target=_add_dependencies, daemon=True, args=(item,)).start()
    
    def resolveDependencies(self, item : Entry, path : str):
        # First step: check for dependency array
        self.dlgProgress.setLabelText("Löse Abhängigkeiten auf...")
        if item.dependencies:
            def _add_dependencies(item : Entry, path : str):
                if item.dependencies:
                    for d in item.dependencies:
                        for i in range(self.ui.lsEntries.count()):
                            if self.ui.lsEntries.item(i).larry.ids["upload"] == d and d not in self.queues["download"].queue:
                                self.dlgProgress.setLabelText(f"Füge Abhängigkeit hinzu: {self.ui.lsEntries.item(i).text()}")
                                _add_dependencies(self.ui.lsEntries.item(i).larry)
                self.queues["download"].put((item, path))
            
            _add_dependencies(item, path)
            return
        else:
            self.queues["download"].put((item, path))
            self.signal_finished.disconnect(self.downloadFinished)
            self.signal_finished.connect(self.resolveDependenciesAfterInstallation)
    
    def download(self, sel : QListWidgetItem = None):
        def _download(item : QListWidgetItem, path : str):
            self.resolveDependencies(item.larry, path)
            self.queues["download"].join()
            if self.errors:
                e = "\n".join(str(i) for i in self.errors)
                self.errors = list()
                self.signal_error.emit(e)
            else:
                self.signal_finished.emit(item.larry, path)
        
        sel = sel or self.ui.lsEntries.currentItem()
        if not sel:
            return
        v = sel.larry.clonkVersion()
        if not self.config.value(f"{v}/directory"):
            return self.displayErrorBox("Kein Clonk-Verzeichnis angegeben!")
        
        self.sender().setEnabled(False)
        self.dlgProgress.open()
        thread = threading.Thread(target=_download, args=(sel, self.config.value(f"{v}/directory")))
        thread.start()

    def downloadError(self, e : str):
        self.displayErrorBox(f"Fehler beim Download: {e}")
        self.ui.btnDownload.setEnabled(True)

    def downloadFinished(self):
        self.dlgProgress.reset()
        self.displayMessageBox("Download erfolgreich!")
        self.ui.btnDownload.setEnabled(True)

    def searchTextChanged(self, text : str):
        for i in range(self.ui.lsEntries.count()):
            self.ui.lsEntries.item(i).setHidden(text not in self.ui.lsEntries.item(i).text())
    
    def cycle(self) -> None:
        print("fired")
        i = self.ui.mainStack.currentIndex()
        self.ui.mainStack.setCurrentIndex(i + 1 if i + 1 < self.ui.mainStack.count() else 0)
    
    # Settings

    def setClonkDir(self):
        f = QFileDialog.getExistingDirectory(self, self.tr("Verzeichnis auswählen"), os.getcwd())
        if f != "":
            v = self.sender().objectName()[3:5]
            self.config.setValue(f"{v}/directory", f)
            getattr(self.dlgSettings, f"lbl{v}Dir").setText(f)

class LarryUploader(QDialog):
    credentials = {
        "username" : "",
        "email" : "",
        "password" : "",
        "token" : ""
        }
    ui = None
    current_upload = None
    
    def __init__(self, parent):
        super().__init__(parent)
        self.ui = loadUi("larry_uploader.ui", self)
        
        self.ui.imgScene = QGraphicsScene()
        self.ui.pctImage = QGraphicsView(self.ui.imgScene)
        self.ui.pctImage.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.pctImage.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.ui.layImage.addWidget(self.ui.pctImage)
        
        self.ui.btnAdvance.clicked.connect(self.advance)
        self.ui.btnBack.clicked.connect(self.back)
        self.ui.btnBack.setEnabled(False)
        
        self.ui.tabs.setCurrentIndex(0)
        
        self.ui.btnChooseImage.clicked.connect(self.chooseImage)
        self.ui.btnChooseFiles.clicked.connect(self.chooseFiles)
    
    def rejectTabChange(self, index : int):
        if index == 0:
            email, password = self.ui.txtEmail.text(), self.ui.txtPassword.text()
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
            title, desc = self.ui.txtTitle.text(), self.ui.txtDescription.toPlainText()
            if not title:
                return "Titel fehlt."
            if not desc:
                return "Beschreibung fehlt."
            
            if [i for i in range(self.parent().ui.lsEntries.count()) if self.parent().ui.lsEntries.item(i).larry.title == title]:
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
            if self.ui.chkAccepted.checkState() != Qt.Checked:
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
            self.ui.btnAdvance.setEnabled(False)
            self.ui.btnBack.setEnabled(False)
            txt = self.rejectTabChange(self.ui.tabs.currentIndex())
            self.ui.btnAdvance.setEnabled(True)
            self.ui.btnBack.setEnabled(True)
            if txt:
                LarryViewer.displayMessageBox(self, txt, "Fehler", QMessageBox.Critical)
                return
        next = self.ui.tabs.currentIndex() + offset
        
        self.ui.btnAdvance.setText(self.tr("Weiter"))
        if next == 0:
            self.ui.btnBack.setEnabled(False)
        elif next == self.ui.tabs.count() - 1:
            self.ui.btnAdvance.setText(self.tr("Fertigstellen"))
        
        self.ui.tabs.setCurrentIndex(self.ui.tabs.currentIndex() + offset)
    
    def chooseImage(self):
        # WARNING: Misusing Entry.picture_url to store a path
        self.current_upload.filename = QFileDialog.getOpenFileName(self, self.tr("Wähle die Bilddatei"), os.getcwd(), self.tr("Images (*.png *.jpg *.jpeg)"))[0]
        self.updateImage(self.current_upload.filename)
    
    def updateImage(self, f : str):
        self.ui.imgScene.clear()
        img = QImage(f).scaled(self.pctImage.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap = QGraphicsPixmapItem(QPixmap.fromImage(img))
        self.ui.imgScene.addItem(pixmap)
        self.ui.pctImage.update()
    
    def chooseFiles(self):
        # WARNING: Misusing Entry.ids["file"] to store file paths
        files = QFileDialog.getOpenFileNames(self, self.tr("Wähle die Dateien"), os.getcwd(), self.tr("Clonk Files (*.c4d *.c4f *.c4b *.c4r *.c4s *.c4m *.c4g *.ocd *.ocf *.ocb *.ocr *.ocs *ocm"))[0]
        for f in files:
            self.ui.lsFiles.addItem(QListWidgetItem(f))
        
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
    QApplication.setOrganizationName("Fulgen")
    QApplication.setOrganizationDomain("https://github.com/Fulgen301")
    QApplication.setApplicationName("LarryViewer")
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)

    viewer = LarryViewer()
    viewer.ui.show()
    #sys.excepthook = lambda e, v, t: QErrorMessage.qtHandler().showMessage("".join(traceback.format_exception(e, v, t)))
    #sys.excepthook = lambda e, v, t: traceback.print_exception(e, v, t)
    sys.exit(app.exec_())
