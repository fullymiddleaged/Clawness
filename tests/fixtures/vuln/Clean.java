// A clean fixture — the enumerator must find NOTHING here. Not compiled/run.
import java.sql.Connection;
import java.sql.PreparedStatement;

public class Clean {
    static String apiKey = System.getenv("API_KEY"); // reference, not a literal

    void lookup(Connection c, String userId) throws Exception {
        // parameterised query — the safe form
        PreparedStatement ps = c.prepareStatement("SELECT * FROM users WHERE id = ?");
        ps.setString(1, userId);
        ps.executeQuery();
    }
}
