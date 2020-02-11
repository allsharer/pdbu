PDBU -- Personal Data Backup Utility -- README
 (v. 3.0)

 Commands:

 pdbu           = Makes a PDBU exclusive backup based on the set interval.
 pdbu --force   = Disregards set interval and forces an On-Demand backup.
 pdbu --help    = Prints this help information.
 pdbu --restore = Fully restores system's /home directory.
 pdbu --log     = Prints the log file to screen.
 pdbu --check   = Checks if backup is due and notifies user via a text box.
                  Intended to be used with startup applications.
 q              = Exit this Help Page.

 Exit Codes:
    0 = Backup completed successfully.
    1 = Backup completed with errors.
    2 = Destinaion media not found.
    3 = Backup interval not reached.
    4 = Bad command line option received.

 Description:

 PDBU will update files that have changed, remove files that have been
 deleted and add any new files that have been created since the last backup.
 The initial backup may take a long time, especially if you have a lot of data stored
 in your system's /home directory. PDBU creates a directory with the host name
 of the machine it's backing up from inside PDBU-Backups. This allows users to
 share the drive with multiple machines.

 The directory structure and all files are stored openly to allow users easy
 access if they only need to retrieve a few files or directories using a file
 manager. Data can be restored with the built in emergency "pdbu restore"
 command if the user wants to roll back to the exact state of /home when the
 last backup was taken.

 A simple log file is added to the PDBU-Backups directory that shows when each
 backup is taken and any errors that might occur. This file is appended
 every time PDBU is run.

 Requirements:

 PDBU is designed to work with Ubuntu and Linux Mint but it should run
 on many other Linux distributions. You need to set the PDBU variable
 (BML) to the destination drive's label. The destination USB drive must be
 formatted to a Linux native file system such as Ext4 to ensure that file
 permissions will be stored unchanged. The USB drive needs to have enough
 free capacity to store all data in /home on all of the machines you want to
 use the drive for.

 Disclaimer:

 THIS SOFTWARE IS CREATED FOR PERSONAL USE BUT MAY BE USED “AS IS” AND ANY
 EXPRESSED OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 DISCLAIMED.  IN NO EVENT SHALL THE OWNER OF THIS SOFTWARE BE LIABLE FOR ANY 
 DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES 
 (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

