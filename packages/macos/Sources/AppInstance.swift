import AppKit
import Darwin

/// One application owns the desktop UI and its MCP runtime for this user.
@MainActor enum AppInstance {
    private static var descriptor: Int32 = -1

    static func acquire() throws -> Bool {
        let directory = "/private/tmp/opengcua-service-\(getuid())"
        if mkdir(directory, 0o700) != 0 && errno != EEXIST {
            throw AppError.message("Cannot create OpenGCUA runtime directory")
        }
        var info = stat()
        guard lstat(directory, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o777 == 0o700 else {
            throw AppError.message("Invalid OpenGCUA runtime directory")
        }
        let fd = open(directory + "/app.lock", O_RDWR | O_CREAT | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw AppError.message("Cannot open OpenGCUA application lock") }
        guard fstat(fd, &info) == 0, info.st_uid == getuid(), info.st_mode & S_IFMT == S_IFREG,
              info.st_mode & 0o777 == 0o600, info.st_nlink == 1 else {
            close(fd)
            throw AppError.message("Invalid OpenGCUA application lock")
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            let busy = errno == EWOULDBLOCK
            close(fd)
            guard busy else { throw AppError.message("Cannot lock OpenGCUA application") }
            NSRunningApplication.runningApplications(withBundleIdentifier: "app.generalist.mac")
                .first { $0.processIdentifier != getpid() && $0.bundleURL == Bundle.main.bundleURL }?
                .activate(options: [.activateAllWindows])
            return false
        }
        _ = fcntl(fd, F_SETFD, FD_CLOEXEC)
        descriptor = fd
        return true
    }
}
