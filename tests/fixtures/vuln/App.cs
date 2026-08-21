// Intentionally vulnerable fixture for tests/test_scan.py. Not compiled/run.
using System;
using System.Data.SqlClient;
using System.Diagnostics;
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;
using System.Security.Cryptography;

public class App
{
    const string ApiKey = "sk_live_0123456789abcdefghij"; // hardcoded-secret

    void Lookup(string userId)
    {
        // sql-injection: interpolated string into SqlCommand
        var cmd = new SqlCommand($"SELECT * FROM users WHERE id = {userId}");
    }

    void Run(string name)
    {
        // command-injection
        Process.Start("cmd.exe", "/c " + name);
    }

    object Load(Stream s)
    {
        // unsafe-deserialization
        return new BinaryFormatter().Deserialize(s);
    }

    void Eval(string code)
    {
        // code-eval
        Microsoft.CodeAnalysis.CSharp.Scripting.CSharpScript.RunAsync(code);
    }

    void Render(System.Web.HttpResponse Response, System.Web.HttpRequest Request)
    {
        // xss
        Response.Write(Request.Query["q"]);
    }

    byte[] Read(System.Web.HttpRequest Request)
    {
        // path-traversal
        return File.ReadAllBytes(Request.Query["path"]);
    }

    byte[] Weak(byte[] pw)
    {
        // weak-crypto
        return MD5.Create().ComputeHash(pw);
    }

    int Token()
    {
        // weak-crypto
        return new Random().Next();
    }

    void Proxy(System.Web.HttpRequest Request)
    {
        // ssrf
        new System.Net.WebClient().DownloadString(Request.Query["url"]);
    }
}
