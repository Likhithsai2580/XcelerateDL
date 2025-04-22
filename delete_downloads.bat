@echo off
echo Deleting downloads folder...
if exist downloads (
    rmdir /s /q downloads
    echo Downloads folder deleted successfully.
) else (
    echo Downloads folder does not exist.
)
pause 