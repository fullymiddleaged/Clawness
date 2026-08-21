// A clean fixture — the enumerator must find NOTHING here. Not compiled/run.
using System.Data.SqlClient;

public class Clean
{
    static string ApiKey = System.Environment.GetEnvironmentVariable("API_KEY");

    void Lookup(SqlConnection c, string userId)
    {
        // parameterised query — the safe form
        var cmd = new SqlCommand("SELECT * FROM users WHERE id = @id", c);
        cmd.Parameters.AddWithValue("@id", userId);
    }
}
