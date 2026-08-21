// Intentionally vulnerable fixture for tests/test_scan.py. Not compiled/run.
import java.security.MessageDigest;
import java.sql.Connection;
import java.io.File;
import java.io.InputStream;
import java.io.ObjectInputStream;
import java.util.Random;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

public class App {
    static String apiKey = "AKIAIOSFODNN7EXAMPLE"; // hardcoded-secret

    void lookup(Connection c, String userId) throws Exception {
        // sql-injection: concat into executeQuery
        c.createStatement().executeQuery("SELECT * FROM users WHERE id = " + userId);
    }

    void run(String name) throws Exception {
        // command-injection
        Runtime.getRuntime().exec("sh -c " + name);
    }

    Object load(InputStream in) throws Exception {
        // unsafe-deserialization
        return new ObjectInputStream(in).readObject();
    }

    void compute(String expr) throws Exception {
        // code-eval: ScriptEngine
        new javax.script.ScriptEngineManager().getEngineByName("js").eval(expr);
    }

    void render(HttpServletResponse resp, HttpServletRequest request) throws Exception {
        // xss: writer print of request data
        resp.getWriter().print(request.getParameter("q"));
    }

    byte[] read(HttpServletRequest request) throws Exception {
        // path-traversal: new File fed request data
        return java.nio.file.Files.readAllBytes(new File(request.getParameter("path")).toPath());
    }

    byte[] weak(String pw) throws Exception {
        // weak-crypto
        return MessageDigest.getInstance("MD5").digest(pw.getBytes());
    }

    int token() {
        // weak-crypto: java.util.Random
        return new Random().nextInt();
    }

    void proxy(HttpServletRequest request) throws Exception {
        // ssrf
        new java.net.URL(request.getParameter("url")).openConnection();
    }
}
